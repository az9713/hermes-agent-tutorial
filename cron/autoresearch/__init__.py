"""
cron/autoresearch — Hermes autoresearch loop.

Entry points:
  run_stage1()   Extract session signals, aggregate skill health, write report.
                 Zero risk: reads state.db, writes skill_metrics.db + report.
                 Nothing in HERMES_HOME/skills/ is modified.

  run_stage2()   Detect anomalies, generate hypotheses via LLM, evaluate via
                 self-play, write pending_patches.json.
                 Low risk: no skill files are modified.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from hermes_constants import get_hermes_home
from cron.autoresearch.signal_extractor import extract_signals
from cron.autoresearch.skill_metrics import (
    already_extracted,
    build_and_store_holdout_cases,
    compute_and_store_skill_health,
    get_operator_confidence_metrics,
    get_skill_health_summary,
    mark_holdout_cases_used,
    record_eval_run,
    get_session_signals,
    upsert_memory_update_proposal,
    open_db,
    record_session_signal,
)
from cron.autoresearch.reporter import generate_report
from cron.autoresearch.anomaly_detector import detect_anomalies
from cron.autoresearch.hypothesis_generator import generate_hypothesis
from cron.autoresearch.self_play_evaluator import evaluate_candidate
from cron.autoresearch.pending_patches import write_pending_patches, read_pending_patches
from cron.autoresearch.pending_memory_updates import write_pending_memory_updates
from cron.autoresearch.memory_anomaly_detector import detect_memory_anomalies
from cron.autoresearch.memory_hypothesis_generator import generate_memory_proposals
from cron.autoresearch.memory_updater import process_memory_updates
from cron.autoresearch.applier import apply_patches
from cron.autoresearch.regression_watch import check_regressions
from cron.autoresearch.digest import generate_digest

logger = logging.getLogger(__name__)


def run_stage1(
    state_db_path: Optional[Path] = None,
    metrics_db_path: Optional[Path] = None,
    report_path: Optional[Path] = None,
    hermes_home: Optional[Path] = None,
    since_hours: int = 24,
) -> str:
    """Run the Stage 1 autoresearch cycle.

    Steps:
      1. Extract session signals from state.db (last since_hours).
      2. Store new signals in skill_metrics.db (skip already-extracted sessions).
      3. Aggregate skill_health rows for today.
      4. Load 7-day rolling skill health summary.
      5. Generate and write nightly_report.md.

    Returns the report text.

    Args:
        state_db_path:  Path to Hermes state.db. Defaults to HERMES_HOME/state.db.
        metrics_db_path: Path to skill_metrics.db. Defaults to
                         HERMES_HOME/autoresearch/skill_metrics.db.
        report_path:    Path for nightly_report.md. Defaults to
                        HERMES_HOME/autoresearch/nightly_report.md.
        hermes_home:    Override HERMES_HOME. Used in tests.
        since_hours:    How many hours back to look for sessions. Default 24.
    """
    logger.info("Autoresearch Stage 1 starting")
    resolved_home = hermes_home or get_hermes_home()
    resolved_metrics_db = metrics_db_path or (
        resolved_home / "autoresearch" / "skill_metrics.db"
    )
    resolved_report_path = report_path or (
        resolved_home / "autoresearch" / "nightly_report.md"
    )

    # 1. Extract signals from state.db
    signals = extract_signals(
        state_db_path=state_db_path,
        since_hours=since_hours,
        hermes_home=hermes_home,
    )
    logger.info("Extracted %d session signal(s)", len(signals))

    # 2. Store new signals (skip duplicates)
    metrics_conn = open_db(resolved_metrics_db)
    new_count = 0
    for signal in signals:
        if not already_extracted(metrics_conn, signal["session_id"]):
            record_session_signal(metrics_conn, signal)
            new_count += 1
    logger.info("Stored %d new signal(s) (%d already present)", new_count, len(signals) - new_count)

    # 3. Aggregate skill_health for today
    health_rows = compute_and_store_skill_health(metrics_conn)
    logger.info("Aggregated skill health for %d skill(s)", len(health_rows))

    # 4. Load 7-day rolling summary
    summary = get_skill_health_summary(metrics_conn, days=7)
    metrics_conn.close()

    # 5. Generate report
    report_text = generate_report(
        session_count=len(signals),
        skill_health=summary,
        report_path=resolved_report_path,
    )
    logger.info("Autoresearch Stage 1 complete. Report written.")
    return report_text


# ---------------------------------------------------------------------------
# Stage 2: Hypothesize + Evaluate
# ---------------------------------------------------------------------------

LlmCall = Callable[[List[Dict[str, Any]], ], str]


def _read_skill_content(skill_name: str, hermes_home: Optional[Path]) -> Optional[str]:
    """Read a skill's SKILL.md. Returns None if the file doesn't exist."""
    home = hermes_home or get_hermes_home()
    skill_md = home / "skills" / skill_name / "SKILL.md"
    if not skill_md.exists():
        return None
    return skill_md.read_text(encoding="utf-8")


def _get_session_task_excerpts(
    metrics_conn,
    skill_name: str,
    days: int = 7,
    max_excerpts: int = 5,
) -> List[str]:
    """Return up to max_excerpts task/correction strings for a skill.

    Pulls raw session_signals for sessions that invoked this skill, extracts
    a best-effort task label. Used to ground synthetic self-play tasks.
    """
    import json as _json
    rows = get_session_signals(metrics_conn)
    excerpts: List[str] = []
    for row in rows:
        try:
            skills = _json.loads(row["skills_invoked"] or "[]")
        except Exception:
            skills = []
        if skill_name in skills:
            snippet = ""
            try:
                snippet_vals = _json.loads(row["correction_snippets"] or "[]")
                if snippet_vals:
                    snippet = str(snippet_vals[0])
            except Exception:
                snippet = ""
            if snippet:
                excerpts.append(snippet)
            else:
                # Fallback for older rows where correction snippets were unavailable.
                excerpts.append(
                    f"Session {row['session_id']}: {row['correction_count']} correction(s)"
                )
        if len(excerpts) >= max_excerpts:
            break
    return excerpts


def run_stage2(
    metrics_db_path: Optional[Path] = None,
    patches_path: Optional[Path] = None,
    hermes_home: Optional[Path] = None,
    llm_call: Optional[LlmCall] = None,
    judge_llm_call: Optional[LlmCall] = None,
    days: int = 7,
    enable_memory_updates: bool = True,
    pending_memory_updates_path: Optional[Path] = None,
    memory_min_confidence: float = 0.7,
    memory_min_evidence: int = 2,
    memory_min_evidence_score: float = 1.25,
    enable_holdout_eval: bool = True,
    holdout_days: int = 30,
    holdout_tasks_per_skill: int = 20,
) -> List[Dict[str, Any]]:
    """Run the Stage 2 autoresearch cycle.

    Steps:
      1. Detect anomalies from skill_metrics.db.
      2. For each anomaly, generate a hypothesis (LLM patch proposal).
      3. Evaluate each hypothesis via self-play (LLM judge).
      4. Write pending_patches.json with all results.

    Returns list of pending patch dicts (same schema as pending_patches.json).

    Args:
        metrics_db_path: Path to skill_metrics.db. Defaults to HERMES_HOME/autoresearch/skill_metrics.db.
        patches_path:    Path for pending_patches.json. Defaults to HERMES_HOME/autoresearch/pending_patches.json.
        hermes_home:     Override HERMES_HOME. Used in tests.
        llm_call:        Injected LLM callable. If None, imports call_llm from
                         agent.auxiliary_client and wraps it.
        days:            Rolling window for anomaly detection. Default 7.
    """
    logger.info("Autoresearch Stage 2 starting")

    # Resolve LLM callable
    if llm_call is None:
        try:
            from agent.auxiliary_client import call_llm as _call_llm  # type: ignore

            def llm_call(messages):
                resp = _call_llm(messages=messages)
                return resp.choices[0].message.content
        except ImportError:
            logger.error("agent.auxiliary_client not available and no llm_call injected")
            raise
    resolved_judge_call = judge_llm_call or llm_call

    resolved_home = hermes_home or get_hermes_home()
    resolved_metrics_db = metrics_db_path or (
        resolved_home / "autoresearch" / "skill_metrics.db"
    )
    resolved_patches_path = patches_path or (
        resolved_home / "autoresearch" / "pending_patches.json"
    )
    memory_updates_path = pending_memory_updates_path or (
        resolved_home / "autoresearch" / "pending_memory_updates.json"
    )

    # 1. Detect anomalies
    metrics_conn = open_db(resolved_metrics_db)
    anomalies = detect_anomalies(metrics_conn, days=days)
    logger.info("Detected %d anomaly(-ies)", len(anomalies))

    # 2-3. Generate hypothesis + evaluate for each anomaly
    pairs: List[Dict[str, Any]] = []
    for anomaly in anomalies:
        skill_name = anomaly["skill_name"]

        # Read current SKILL.md
        skill_content = _read_skill_content(skill_name, resolved_home)
        if skill_content is None:
            logger.warning("SKILL.md not found for '%s' — skipping", skill_name)
            continue

        # Gather session excerpts to ground self-play tasks
        excerpts = _get_session_task_excerpts(metrics_conn, skill_name, days=days)
        holdout_tasks: List[str] = []
        if enable_holdout_eval:
            holdout_tasks = build_and_store_holdout_cases(
                metrics_conn,
                skill_name,
                days=holdout_days,
                limit=holdout_tasks_per_skill,
                exclude_texts=excerpts,
            )

        # Generate hypothesis
        candidate = generate_hypothesis(
            anomaly=anomaly,
            skill_content=skill_content,
            session_excerpts=excerpts,
            llm_call=llm_call,
        )
        if candidate is None:
            logger.info("No hypothesis generated for '%s' — skipping", skill_name)
            continue

        # Evaluate via self-play
        eval_result = evaluate_candidate(
            candidate_patch=candidate,
            skill_content_old=skill_content,
            session_tasks=excerpts,
            llm_call=llm_call,
            judge_llm_call=resolved_judge_call,
            holdout_tasks=holdout_tasks,
        )
        if holdout_tasks:
            mark_holdout_cases_used(metrics_conn, skill_name, holdout_tasks)
        record_eval_run(
            metrics_conn,
            skill_name=skill_name,
            anomaly_type=anomaly["anomaly_type"],
            status=eval_result["status"],
            self_play_token_delta=float(eval_result.get("token_delta", 0.0)),
            self_play_quality_delta=float(eval_result.get("quality_delta", 0.0)),
            holdout_quality_delta=float(eval_result.get("holdout_quality_delta", 0.0)),
            holdout_pass=bool(eval_result.get("holdout_pass", False)),
            rubric_pass_rate_old=float(eval_result.get("rubric_pass_rate_old", 0.0)),
            rubric_pass_rate_new=float(eval_result.get("rubric_pass_rate_new", 0.0)),
            dual_judge_disagreement=bool(eval_result.get("dual_judge_disagreement", False)),
        )
        logger.info(
            "Skill '%s': status=%s token_delta=%.2f quality_delta=%.2f",
            skill_name,
            eval_result["status"],
            eval_result["token_delta"],
            eval_result["quality_delta"],
        )

        pairs.append({"candidate": candidate, "eval_result": eval_result})

    # 4. Write pending_patches.json
    write_pending_patches(pairs, path=resolved_patches_path)

    # 5. Memory anomaly detection + proposal generation (built-in memory only)
    if enable_memory_updates:
        anomalies_mem = detect_memory_anomalies(
            metrics_conn,
            resolved_home,
            days=days,
            min_evidence=memory_min_evidence,
            min_evidence_score=memory_min_evidence_score,
        )
        proposals = generate_memory_proposals(
            anomalies_mem,
            llm_call=llm_call,
            min_confidence=memory_min_confidence,
            min_evidence=memory_min_evidence,
            min_evidence_score=memory_min_evidence_score,
        )
        write_pending_memory_updates(
            proposals,
            path=memory_updates_path,
        )
        for p in proposals:
            upsert_memory_update_proposal(
                metrics_conn,
                target=p["target"],
                action=p["action"],
                old_text=p["old_text"],
                new_content=p.get("content", ""),
                reason=p.get("reason", ""),
                confidence=float(p.get("confidence", 0.0)),
                evidence_count=int(p.get("evidence_count", 0)),
                evidence_score=float(p.get("evidence_score", 0.0)),
            )
        logger.info(
            "Autoresearch Stage 2 memory proposals: anomalies=%d proposals=%d",
            len(anomalies_mem),
            len(proposals),
        )
    else:
        write_pending_memory_updates([], path=memory_updates_path)

    metrics_conn.close()
    logger.info(
        "Autoresearch Stage 2 complete. %d patch(es) evaluated.",
        len(pairs),
    )
    return pairs


# ---------------------------------------------------------------------------
# Stage 3: Apply + Recover
# ---------------------------------------------------------------------------

def run_stage3(
    metrics_db_path: Optional[Path] = None,
    patches_path: Optional[Path] = None,
    digest_path: Optional[Path] = None,
    hermes_home: Optional[Path] = None,
    dry_run: bool = False,
    run_regression_watch: bool = True,
    run_memory_apply: bool = True,
) -> str:
    """Run the Stage 3 autoresearch cycle.

    Steps:
      1. Read pending_patches.json (written by Stage 2).
      2. Apply accepted patches (with recency lock, stale-patch guard, dry_run).
      3. Run regression watch for previously applied patches.
      4. Generate and write nightly_digest.md.

    Returns the digest text.

    Args:
        metrics_db_path:      Path to skill_metrics.db.
        patches_path:         Path to pending_patches.json.
        digest_path:          Path for nightly_digest.md.
        hermes_home:          Override HERMES_HOME. Used in tests.
        dry_run:              If True, log what would happen but write nothing to skills/.
        run_regression_watch: If False, skip regression watch (useful for first-run).
    """
    logger.info("Autoresearch Stage 3 starting (dry_run=%s)", dry_run)
    resolved_home = hermes_home or get_hermes_home()
    resolved_metrics_db = metrics_db_path or (
        resolved_home / "autoresearch" / "skill_metrics.db"
    )
    resolved_patches_path = patches_path or (
        resolved_home / "autoresearch" / "pending_patches.json"
    )
    resolved_digest_path = digest_path or (
        resolved_home / "autoresearch" / "nightly_digest.md"
    )

    metrics_conn = open_db(resolved_metrics_db)

    # 1. Read pending_patches.json
    patches = read_pending_patches(path=resolved_patches_path)
    logger.info("Stage 3: read %d pending patch(es)", len(patches))

    # 2. Apply accepted patches
    apply_results = apply_patches(
        patches=patches,
        metrics_conn=metrics_conn,
        hermes_home=resolved_home,
        dry_run=dry_run,
    )
    applied_count = sum(1 for r in apply_results if r["status"] == "applied")
    logger.info("Stage 3: applied %d patch(es)", applied_count)

    # 3. Regression watch
    watch_results = []
    if run_regression_watch:
        watch_results = check_regressions(
            metrics_conn=metrics_conn,
            hermes_home=resolved_home,
        )
        logger.info("Stage 3: regression watch examined %d patch(es)", len(watch_results))

    memory_results: Dict[str, List[Dict[str, Any]]] = {
        "proposed": [],
        "applied": [],
        "results": [],
    }
    if run_memory_apply and not dry_run:
        memory_results = process_memory_updates(
            metrics_conn=metrics_conn,
            hermes_home=resolved_home,
            anomaly_days=7,
            min_revalidation_evidence=2,
        )
        logger.info(
            "Stage 3: memory updates processed=%d applied=%d proposed=%d",
            len(memory_results["results"]),
            len(memory_results["applied"]),
            len(memory_results["proposed"]),
        )

    operator_confidence = get_operator_confidence_metrics(metrics_conn, days=30)
    metrics_conn.close()

    # 4. Generate digest
    digest_text = generate_digest(
        apply_results=apply_results,
        watch_results=watch_results,
        pending_patches=patches,
        report_path=resolved_digest_path,
        memory_proposed=memory_results["proposed"],
        memory_applied=memory_results["applied"],
        memory_results=memory_results["results"],
        operator_confidence=operator_confidence,
    )
    logger.info("Autoresearch Stage 3 complete. Digest written.")
    return digest_text
