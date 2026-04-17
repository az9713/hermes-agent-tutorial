import json
from datetime import datetime, timezone, timedelta

from cron.autoresearch import run_stage2, run_stage3
from cron.autoresearch.skill_metrics import (
    open_db,
    record_session_signal,
    upsert_skill_health,
)
from tools.memory_tool import ENTRY_DELIMITER


SKILL_NAME = "bad-skill"
SKILL_OLD = "Always do the right thing."
SKILL_NEW = "Always do the right thing: confirm branch before push."


def _write_memory(tmp_path, entries):
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text(
        ENTRY_DELIMITER.join(entries),
        encoding="utf-8",
    )


def _seed_signal(conn, snippet: str, skills=None, session_id="s1"):
    record_session_signal(
        conn,
        {
            "session_id": session_id,
            "session_date": "2099-01-01",
            "total_tokens": 400,
            "tool_call_count": 2,
            "correction_count": 1,
            "correction_snippets": [snippet],
            "completion_flag": False,
            "skills_invoked": skills or [],
        },
    )


def _memory_only_llm(messages):
    last = messages[-1]["content"]
    if "Current entry:" in last and "Propose exactly one update" in last:
        return json.dumps(
            {
                "action": "replace",
                "target": "memory",
                "old_text": "Always use branch main for deploy.",
                "content": "Use release branches for deploy.",
                "reason": "deploy policy changed",
                "confidence": 0.93,
            }
        )
    return "{}"


def _mixed_skill_memory_llm(messages):
    last = messages[-1]["content"]
    if "Current entry:" in last and "Propose exactly one update" in last:
        return json.dumps(
            {
                "action": "replace",
                "target": "memory",
                "old_text": "Always use branch main for deploy.",
                "content": "Use release branches for deploy.",
                "reason": "deploy policy changed",
                "confidence": 0.9,
            }
        )
    if "Propose a targeted patch" in last or "Identify the specific gap" in last:
        return json.dumps(
            {
                "patch": {
                    "old_string": SKILL_OLD,
                    "new_string": SKILL_NEW,
                    "reason": "make rule concrete",
                }
            }
        )
    if last.startswith("Rephrase"):
        return "Alternative task description."
    if "## Skill" in last:
        if SKILL_NEW in last:
            return "Short. " * 5
        return "Long response. " * 20
    if "Score the following" in last:
        if "Short." in last:
            return "8"
        return "6"
    return "5"


def test_memory_two_phase_propose_then_apply_next_run(tmp_path):
    metrics_db = tmp_path / "metrics.db"
    _write_memory(tmp_path, ["Always use branch main for deploy."])
    conn = open_db(metrics_db)
    _seed_signal(conn, "that's wrong, never use branch main for deploy")
    _seed_signal(conn, "that's incorrect, never use branch main for deploy", session_id="s2")
    conn.close()

    run_stage2(
        metrics_db_path=metrics_db,
        hermes_home=tmp_path,
        llm_call=_memory_only_llm,
        days=7,
    )
    pending_file = tmp_path / "autoresearch" / "pending_memory_updates.json"
    pending = json.loads(pending_file.read_text(encoding="utf-8"))
    assert len(pending) == 1

    conn = open_db(metrics_db)
    row = conn.execute(
        "SELECT id, status, apply_after, applied_at FROM autoresearch_memory_updates"
    ).fetchone()
    assert row["status"] == "proposed"
    assert row["applied_at"] is None
    update_id = int(row["id"])
    conn.close()

    run_stage3(
        metrics_db_path=metrics_db,
        hermes_home=tmp_path,
        run_regression_watch=False,
    )
    conn = open_db(metrics_db)
    row = conn.execute(
        "SELECT status FROM autoresearch_memory_updates WHERE id = ?",
        (update_id,),
    ).fetchone()
    assert row["status"] == "proposed"

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute(
        "UPDATE autoresearch_memory_updates SET apply_after = ? WHERE id = ?",
        (past, update_id),
    )
    conn.commit()
    conn.close()

    digest = run_stage3(
        metrics_db_path=metrics_db,
        hermes_home=tmp_path,
        run_regression_watch=False,
    )

    conn = open_db(metrics_db)
    row = conn.execute(
        "SELECT status, applied_at FROM autoresearch_memory_updates WHERE id = ?",
        (update_id,),
    ).fetchone()
    conn.close()
    assert row["status"] == "applied"
    assert row["applied_at"] is not None
    memory_text = (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert "Use release branches for deploy." in memory_text
    assert "## Applied memory" in digest


def test_mixed_skill_patch_and_memory_proposal_no_cross_regression(tmp_path):
    metrics_db = tmp_path / "metrics.db"

    skill_dir = tmp_path / "skills" / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"# {SKILL_NAME}\n\n## Rules\n- {SKILL_OLD}\n",
        encoding="utf-8",
    )

    _write_memory(tmp_path, ["Always use branch main for deploy."])
    conn = open_db(metrics_db)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    upsert_skill_health(
        conn,
        SKILL_NAME,
        today,
        invocation_count=5,
        avg_tokens=1200.0,
        correction_rate=0.6,
        completion_rate=0.8,
    )
    _seed_signal(
        conn,
        "that's wrong, never use branch main for deploy",
        skills=[SKILL_NAME],
    )
    _seed_signal(
        conn,
        "that's incorrect, never use branch main for deploy",
        skills=[SKILL_NAME],
        session_id="s2",
    )
    conn.close()

    run_stage2(
        metrics_db_path=metrics_db,
        hermes_home=tmp_path,
        llm_call=_mixed_skill_memory_llm,
        days=7,
    )
    patches = json.loads((tmp_path / "autoresearch" / "pending_patches.json").read_text(encoding="utf-8"))
    mem_updates = json.loads(
        (tmp_path / "autoresearch" / "pending_memory_updates.json").read_text(encoding="utf-8")
    )
    assert any(p.get("skill_name") == SKILL_NAME for p in patches)
    assert len(mem_updates) >= 1

    run_stage3(
        metrics_db_path=metrics_db,
        hermes_home=tmp_path,
        run_regression_watch=False,
    )
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    memory_text = (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert SKILL_NEW in skill_text
    assert "Always use branch main for deploy." in memory_text

    conn = open_db(metrics_db)
    row = conn.execute(
        "SELECT status FROM autoresearch_memory_updates ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row["status"] == "proposed"


def test_profile_mode_defaults_use_hermes_home_env(tmp_path, monkeypatch):
    profile_home = tmp_path / ".hermes" / "profiles" / "coder"
    profile_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    run_stage2(
        llm_call=lambda _messages: "{}",
        enable_memory_updates=False,
    )
    run_stage3(
        dry_run=True,
        run_regression_watch=False,
        run_memory_apply=False,
    )

    assert (profile_home / "autoresearch" / "skill_metrics.db").exists()
    assert (profile_home / "autoresearch" / "pending_patches.json").exists()
    assert (profile_home / "autoresearch" / "pending_memory_updates.json").exists()
    assert (profile_home / "autoresearch" / "nightly_digest.md").exists()
