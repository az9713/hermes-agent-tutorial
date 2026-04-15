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

from cron.autoresearch.signal_extractor import extract_signals
from cron.autoresearch.skill_metrics import (
    already_extracted,
    compute_and_store_skill_health,
    get_skill_health_summary,
    get_session_signals,
    open_db,
    record_session_signal,
)
from cron.autoresearch.reporter import generate_report
from cron.autoresearch.anomaly_detector import detect_anomalies
from cron.autoresearch.hypothesis_generator import generate_hypothesis
from cron.autoresearch.self_play_evaluator import evaluate_candidate
from cron.autoresearch.pending_patches import write_pending_patches, read_pending_patches
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

    # 1. Extract signals from state.db
    signals = extract_signals(
        state_db_path=state_db_path,
        since_hours=since_hours,
        hermes_home=hermes_home,
    )
    logger.info("Extracted %d session signal(s)", len(signals))

    # 2. Store new signals (skip duplicates)
    metrics_conn = open_db(metrics_db_path)
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
        report_path=report_path,
    )
    logger.info("Autoresearch Stage 1 complete. Report written.")
    return report_text


# ---------------------------------------------------------------------------
# Stage 2: Hypothesize + Evaluate
# ---------------------------------------------------------------------------

LlmCall = Callable[[List[Dict[str, Any]], ], str]


def _read_skill_content(skill_name: str, hermes_home: Optional[Path]) -> Optional[str]:
    """Read a skill's SKILL.md. Returns None if the file doesn't exist."""
    home = hermes_home or Path.home() / ".hermes"
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
            # Use session_id as a placeholder task description (real transcript
            # not available here — Stage 2 uses this as a seed for rephrase)
            excerpts.append(f"Session {row['session_id']}: {row['correction_count']} correction(s)")
        if len(excerpts) >= max_excerpts:
            break
    return excerpts


def run_stage2(
    metrics_db_path: Optional[Path] = None,
    patches_path: Optional[Path] = None,
    hermes_home: Optional[Path] = None,
    llm_call: Optional[LlmCall] = None,
    days: int = 7,
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

    # 1. Detect anomalies
    metrics_conn = open_db(metrics_db_path)
    anomalies = detect_anomalies(metrics_conn, days=days)
    logger.info("Detected %d anomaly(-ies)", len(anomalies))

    if not anomalies:
        logger.info("No anomalies detected — writing empty pending_patches.json")
        write_pending_patches([], path=patches_path)
        metrics_conn.close()
        return []

    # 2-3. Generate hypothesis + evaluate for each anomaly
    pairs: List[Dict[str, Any]] = []
    for anomaly in anomalies:
        skill_name = anomaly["skill_name"]

        # Read current SKILL.md
        skill_content = _read_skill_content(skill_name, hermes_home)
        if skill_content is None:
            logger.warning("SKILL.md not found for '%s' — skipping", skill_name)
            continue

        # Gather session excerpts to ground self-play tasks
        excerpts = _get_session_task_excerpts(metrics_conn, skill_name, days=days)

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
        )
        logger.info(
            "Skill '%s': status=%s token_delta=%.2f quality_delta=%.2f",
            skill_name,
            eval_result["status"],
            eval_result["token_delta"],
            eval_result["quality_delta"],
        )

        pairs.append({"candidate": candidate, "eval_result": eval_result})

    metrics_conn.close()

    # 4. Write pending_patches.json
    write_pending_patches(pairs, path=patches_path)
    logger.info("Autoresearch Stage 2 complete. %d patch(es) evaluated.", len(pairs))
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

    metrics_conn = open_db(metrics_db_path)

    # 1. Read pending_patches.json
    patches = read_pending_patches(path=patches_path)
    logger.info("Stage 3: read %d pending patch(es)", len(patches))

    # 2. Apply accepted patches
    apply_results = apply_patches(
        patches=patches,
        metrics_conn=metrics_conn,
        hermes_home=hermes_home or Path.home() / ".hermes",
        dry_run=dry_run,
    )
    applied_count = sum(1 for r in apply_results if r["status"] == "applied")
    logger.info("Stage 3: applied %d patch(es)", applied_count)

    # 3. Regression watch
    watch_results = []
    if run_regression_watch:
        watch_results = check_regressions(
            metrics_conn=metrics_conn,
            hermes_home=hermes_home or Path.home() / ".hermes",
        )
        logger.info("Stage 3: regression watch examined %d patch(es)", len(watch_results))

    metrics_conn.close()

    # 4. Generate digest
    digest_text = generate_digest(
        apply_results=apply_results,
        watch_results=watch_results,
        pending_patches=patches,
        report_path=digest_path,
    )
    logger.info("Autoresearch Stage 3 complete. Digest written.")
    return digest_text
