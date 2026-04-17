from cron.autoresearch import run_stage3
from cron.autoresearch.skill_metrics import (
    open_db,
    record_session_signal,
    upsert_memory_update_proposal,
)


def _run(tmp_path, **kwargs) -> str:
    return run_stage3(
        metrics_db_path=kwargs.pop("metrics_db_path", tmp_path / "metrics.db"),
        patches_path=kwargs.pop("patches_path", tmp_path / "pending_patches.json"),
        digest_path=kwargs.pop("digest_path", tmp_path / "nightly_digest.md"),
        hermes_home=tmp_path,
        **kwargs,
    )


def _seed_memory_signal(conn, session_id: str):
    record_session_signal(
        conn,
        {
            "session_id": session_id,
            "session_date": "2099-01-01",
            "total_tokens": 120,
            "tool_call_count": 1,
            "correction_count": 1,
            "correction_snippets": ["that's wrong, never use branch main for deploy"],
            "completion_flag": False,
            "skills_invoked": [],
        },
    )
    record_session_signal(
        conn,
        {
            "session_id": f"{session_id}-b",
            "session_date": "2099-01-01",
            "total_tokens": 140,
            "tool_call_count": 1,
            "correction_count": 1,
            "correction_snippets": ["that's incorrect, never use branch main for deploy"],
            "completion_flag": False,
            "skills_invoked": [],
        },
    )


def test_due_memory_update_applied(tmp_path):
    (tmp_path / "pending_patches.json").write_text("[]", encoding="utf-8")
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text("Always use branch main for deploy.", encoding="utf-8")

    metrics_db = tmp_path / "metrics.db"
    conn = open_db(metrics_db)
    _seed_memory_signal(conn, "m1")
    update_id = upsert_memory_update_proposal(
        conn,
        target="memory",
        action="replace",
        old_text="Always use branch main for deploy.",
        new_content="Use release branches for deploy.",
        reason="policy changed",
        confidence=0.9,
        evidence_count=3,
        apply_delay_hours=0,
    )
    conn.close()

    digest = _run(tmp_path, metrics_db_path=metrics_db, run_regression_watch=False)
    memory_text = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "Use release branches for deploy." in memory_text
    assert "## Applied memory" in digest

    conn = open_db(metrics_db)
    row = conn.execute(
        "SELECT status FROM autoresearch_memory_updates WHERE id = ?",
        (update_id,),
    ).fetchone()
    conn.close()
    assert row["status"] == "applied"


def test_memory_apply_can_be_disabled(tmp_path):
    (tmp_path / "pending_patches.json").write_text("[]", encoding="utf-8")
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text("Always use branch main for deploy.", encoding="utf-8")

    metrics_db = tmp_path / "metrics.db"
    conn = open_db(metrics_db)
    _seed_memory_signal(conn, "m2")
    update_id = upsert_memory_update_proposal(
        conn,
        target="memory",
        action="replace",
        old_text="Always use branch main for deploy.",
        new_content="Use release branches for deploy.",
        reason="policy changed",
        confidence=0.9,
        evidence_count=3,
        apply_delay_hours=0,
    )
    conn.close()

    _run(
        tmp_path,
        metrics_db_path=metrics_db,
        run_regression_watch=False,
        run_memory_apply=False,
    )
    conn = open_db(metrics_db)
    row = conn.execute(
        "SELECT status FROM autoresearch_memory_updates WHERE id = ?",
        (update_id,),
    ).fetchone()
    conn.close()
    assert row["status"] == "proposed"
