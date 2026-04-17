"""
Tests for cron/autoresearch/skill_metrics.py — the SQLite DB layer.

What we're testing
──────────────────
skill_metrics.py owns the persistent store used by Stage 1. These tests verify:

1. open_db() creates the three required tables and returns a usable connection.
2. record_session_signal() stores a signal row; skills_invoked is JSON-serialised.
3. already_extracted() returns True only after a signal has been stored.
4. compute_and_store_skill_health() correctly aggregates from session_signals rows:
     - invocation_count, avg_tokens, correction_rate, completion_rate are correct.
     - Each (skill, date) produces exactly one skill_health row (INSERT OR REPLACE).
5. get_skill_health_summary() returns the rolling N-day aggregate sorted by
   correction_rate descending (worst-first).
6. Duplicate-free ingestion: inserting the same session_id twice doesn't double-count.

Why these tests matter
──────────────────────
The DB layer is the source of truth for all Stage 1 metrics. If aggregation is wrong,
the report flags the wrong skills and every downstream stage is corrupted.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cron.autoresearch.skill_metrics import (
    already_extracted,
    build_and_store_holdout_cases,
    compute_and_store_skill_health,
    get_due_memory_updates,
    get_memory_update_counts,
    get_operator_confidence_metrics,
    get_skill_health_summary,
    list_memory_updates,
    open_db,
    record_autoresearch_patch,
    record_eval_run,
    record_session_signal,
    update_memory_update_status,
    upsert_memory_update_proposal,
    upsert_skill_health,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """An in-memory-equivalent DB using a temp file. Returns an open connection."""
    conn = open_db(tmp_path / "skill_metrics.db")
    yield conn
    conn.close()


def make_signal(
    session_id: str = "s1",
    session_date: str = "2026-01-01",
    total_tokens: int = 1000,
    tool_call_count: int = 5,
    correction_count: int = 0,
    completion_flag: bool = True,
    skills_invoked: list = None,
) -> dict:
    return {
        "session_id": session_id,
        "session_date": session_date,
        "total_tokens": total_tokens,
        "tool_call_count": tool_call_count,
        "correction_count": correction_count,
        "completion_flag": completion_flag,
        "skills_invoked": skills_invoked or [],
    }


# ── Tests: open_db ────────────────────────────────────────────────────────────

class TestOpenDb:
    def test_creates_session_signals_table(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='session_signals'"
        ).fetchone()
        assert row is not None

    def test_creates_skill_health_table(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_health'"
        ).fetchone()
        assert row is not None

    def test_creates_autoresearch_patches_table(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='autoresearch_patches'"
        ).fetchone()
        assert row is not None

    def test_creates_autoresearch_memory_updates_table(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='autoresearch_memory_updates'"
        ).fetchone()
        assert row is not None

    def test_idempotent_when_called_twice(self, tmp_path):
        """open_db on an existing DB doesn't error or drop tables."""
        path = tmp_path / "metrics.db"
        conn1 = open_db(path)
        conn1.close()
        conn2 = open_db(path)
        row = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='session_signals'"
        ).fetchone()
        conn2.close()
        assert row is not None


# ── Tests: record_session_signal ─────────────────────────────────────────────

class TestRecordSessionSignal:
    def test_inserts_a_row(self, db):
        record_session_signal(db, make_signal("sess-1"))
        count = db.execute("SELECT COUNT(*) FROM session_signals").fetchone()[0]
        assert count == 1

    def test_skills_invoked_stored_as_json(self, db):
        record_session_signal(db, make_signal("sess-1", skills_invoked=["git-workflow", "web-search"]))
        row = db.execute("SELECT skills_invoked FROM session_signals").fetchone()
        parsed = json.loads(row[0])
        assert parsed == ["git-workflow", "web-search"]

    def test_empty_skills_invoked(self, db):
        record_session_signal(db, make_signal("sess-1", skills_invoked=[]))
        row = db.execute("SELECT skills_invoked FROM session_signals").fetchone()
        assert json.loads(row[0]) == []

    def test_completion_flag_stored_as_int(self, db):
        record_session_signal(db, make_signal("sess-1", completion_flag=True))
        row = db.execute("SELECT completion_flag FROM session_signals").fetchone()
        assert row[0] == 1

    def test_replace_on_duplicate_session_id(self, db):
        """INSERT OR REPLACE: duplicate session_id replaces the row, not appends."""
        record_session_signal(db, make_signal("sess-1", total_tokens=100))
        record_session_signal(db, make_signal("sess-1", total_tokens=200))
        count = db.execute("SELECT COUNT(*) FROM session_signals").fetchone()[0]
        tokens = db.execute("SELECT total_tokens FROM session_signals").fetchone()[0]
        assert count == 1
        assert tokens == 200


# ── Tests: already_extracted ──────────────────────────────────────────────────

class TestAlreadyExtracted:
    def test_returns_false_before_insert(self, db):
        assert not already_extracted(db, "sess-unknown")

    def test_returns_true_after_insert(self, db):
        record_session_signal(db, make_signal("sess-1"))
        assert already_extracted(db, "sess-1")

    def test_only_matches_exact_id(self, db):
        record_session_signal(db, make_signal("sess-1"))
        assert not already_extracted(db, "sess-")
        assert not already_extracted(db, "sess-10")


# ── Tests: compute_and_store_skill_health ─────────────────────────────────────

class TestComputeAndStoreSkillHealth:
    def test_empty_when_no_signals(self, db):
        result = compute_and_store_skill_health(db, for_date="2026-01-01")
        assert result == []

    def test_single_session_single_skill(self, db):
        record_session_signal(db, make_signal(
            "s1", session_date="2026-01-01",
            total_tokens=2000, correction_count=0, completion_flag=True,
            skills_invoked=["git-workflow"],
        ))
        result = compute_and_store_skill_health(db, for_date="2026-01-01")
        assert len(result) == 1
        r = result[0]
        assert r["skill_name"] == "git-workflow"
        assert r["invocation_count"] == 1
        assert r["avg_tokens"] == 2000
        assert r["correction_rate"] == 0.0
        assert r["completion_rate"] == 1.0

    def test_correction_rate_calculation(self, db):
        """2 sessions, 1 with correction → correction_rate = 0.5."""
        for i, corr in enumerate([0, 1]):
            record_session_signal(db, make_signal(
                f"s{i}", session_date="2026-01-01",
                correction_count=corr, skills_invoked=["my-skill"],
            ))
        result = compute_and_store_skill_health(db, for_date="2026-01-01")
        assert result[0]["correction_rate"] == pytest.approx(0.5)

    def test_completion_rate_calculation(self, db):
        """3 sessions, 2 complete → completion_rate = 2/3."""
        for i, flag in enumerate([True, True, False]):
            record_session_signal(db, make_signal(
                f"s{i}", session_date="2026-01-01",
                completion_flag=flag, skills_invoked=["my-skill"],
            ))
        result = compute_and_store_skill_health(db, for_date="2026-01-01")
        assert result[0]["completion_rate"] == pytest.approx(2 / 3)

    def test_multiple_skills_bucketed_separately(self, db):
        record_session_signal(db, make_signal(
            "s1", session_date="2026-01-01", skills_invoked=["skill-a"],
        ))
        record_session_signal(db, make_signal(
            "s2", session_date="2026-01-01", skills_invoked=["skill-b"],
        ))
        record_session_signal(db, make_signal(
            "s3", session_date="2026-01-01", skills_invoked=["skill-a", "skill-b"],
        ))
        result = compute_and_store_skill_health(db, for_date="2026-01-01")
        names = {r["skill_name"] for r in result}
        assert names == {"skill-a", "skill-b"}
        # skill-a appears in s1 and s3 → 2 invocations
        skill_a = next(r for r in result if r["skill_name"] == "skill-a")
        assert skill_a["invocation_count"] == 2

    def test_skill_health_rows_written_to_db(self, db):
        record_session_signal(db, make_signal(
            "s1", session_date="2026-01-01", skills_invoked=["git-workflow"],
        ))
        compute_and_store_skill_health(db, for_date="2026-01-01")
        count = db.execute(
            "SELECT COUNT(*) FROM skill_health WHERE skill_name='git-workflow'"
        ).fetchone()[0]
        assert count == 1

    def test_idempotent_rerun_replaces_not_appends(self, db):
        """Running compute twice on the same date doesn't double the rows."""
        record_session_signal(db, make_signal(
            "s1", session_date="2026-01-01", skills_invoked=["git-workflow"],
        ))
        compute_and_store_skill_health(db, for_date="2026-01-01")
        compute_and_store_skill_health(db, for_date="2026-01-01")
        count = db.execute(
            "SELECT COUNT(*) FROM skill_health WHERE skill_name='git-workflow'"
        ).fetchone()[0]
        assert count == 1


# ── Tests: get_skill_health_summary ──────────────────────────────────────────

class TestGetSkillHealthSummary:
    def test_empty_when_no_health_rows(self, db):
        result = get_skill_health_summary(db, days=7)
        assert result == []

    def test_returns_within_days_window(self, db):
        today = date.today().isoformat()
        upsert_skill_health(db, "recent-skill", today, 5, 1000.0, 0.2, 0.8)
        result = get_skill_health_summary(db, days=7)
        assert len(result) == 1
        assert result[0]["skill_name"] == "recent-skill"

    def test_excludes_old_rows(self, db):
        old_date = (date.today() - timedelta(days=30)).isoformat()
        upsert_skill_health(db, "old-skill", old_date, 5, 1000.0, 0.2, 0.8)
        result = get_skill_health_summary(db, days=7)
        assert result == []

    def test_sorted_by_correction_rate_descending(self, db):
        today = date.today().isoformat()
        upsert_skill_health(db, "good-skill", today, 10, 500.0, 0.1, 0.9)
        upsert_skill_health(db, "bad-skill", today, 10, 2000.0, 0.6, 0.4)
        result = get_skill_health_summary(db, days=7)
        assert result[0]["skill_name"] == "bad-skill"
        assert result[1]["skill_name"] == "good-skill"

    def test_aggregates_multiple_days(self, db):
        """Two rows for the same skill on different days aggregate into one."""
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        upsert_skill_health(db, "my-skill", today, 4, 1000.0, 0.25, 0.75)
        upsert_skill_health(db, "my-skill", yesterday, 6, 2000.0, 0.5, 0.5)
        result = get_skill_health_summary(db, days=7)
        assert len(result) == 1
        r = result[0]
        assert r["total_invocations"] == 10
        # Weighted correction_rate: (0.25*4 + 0.5*6) / 10 = (1+3)/10 = 0.4
        assert r["correction_rate"] == pytest.approx(0.4)


class TestMemoryUpdateLifecycle:
    def test_upsert_inserts_new_proposal(self, db):
        row_id = upsert_memory_update_proposal(
            db,
            target="memory",
            action="replace",
            old_text="old fact",
            new_content="new fact",
            reason="policy changed",
            confidence=0.91,
            evidence_count=3,
            apply_delay_hours=24,
        )
        rows = list_memory_updates(db)
        assert len(rows) == 1
        assert rows[0]["id"] == row_id
        assert rows[0]["status"] == "proposed"
        assert rows[0]["apply_after"] >= rows[0]["first_seen_at"]

    def test_upsert_dedupes_open_rows(self, db):
        first = upsert_memory_update_proposal(
            db,
            target="memory",
            action="replace",
            old_text="old fact",
            new_content="new fact",
            reason="v1",
            confidence=0.7,
            evidence_count=2,
            apply_delay_hours=24,
        )
        second = upsert_memory_update_proposal(
            db,
            target="memory",
            action="replace",
            old_text="old fact",
            new_content="new fact",
            reason="v2",
            confidence=0.95,
            evidence_count=5,
            apply_delay_hours=24,
        )
        rows = list_memory_updates(db)
        assert first == second
        assert len(rows) == 1
        assert rows[0]["reason"] == "v2"
        assert rows[0]["confidence"] == pytest.approx(0.95)
        assert rows[0]["evidence_count"] == 5

    def test_get_due_memory_updates_returns_only_open_due_rows(self, db):
        due_id = upsert_memory_update_proposal(
            db,
            target="memory",
            action="remove",
            old_text="obsolete",
            new_content="",
            reason="obsolete",
            confidence=0.8,
            evidence_count=2,
            apply_delay_hours=24,
        )
        _ = upsert_memory_update_proposal(
            db,
            target="user",
            action="replace",
            old_text="timezone pst",
            new_content="timezone pt",
            reason="correction",
            confidence=0.8,
            evidence_count=2,
            apply_delay_hours=24,
        )
        past_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.execute(
            "UPDATE autoresearch_memory_updates SET apply_after = ? WHERE id = ?",
            (past_iso, due_id),
        )
        db.commit()

        due = get_due_memory_updates(db)
        assert len(due) == 1
        assert due[0]["id"] == due_id

    def test_update_status_sets_applied_at(self, db):
        row_id = upsert_memory_update_proposal(
            db,
            target="memory",
            action="replace",
            old_text="old fact",
            new_content="new fact",
            reason="policy changed",
            confidence=0.9,
            evidence_count=3,
            apply_delay_hours=0,
        )
        update_memory_update_status(db, row_id, "applied", set_applied_at=True)
        row = db.execute(
            "SELECT status, applied_at FROM autoresearch_memory_updates WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert row["status"] == "applied"
        assert row["applied_at"] is not None

    def test_count_map_includes_statuses(self, db):
        row_id = upsert_memory_update_proposal(
            db,
            target="memory",
            action="remove",
            old_text="x",
            new_content="",
            reason="x",
            confidence=0.9,
            evidence_count=2,
            apply_delay_hours=0,
        )
        update_memory_update_status(db, row_id, "needs_review", error="ambiguous")
        counts = get_memory_update_counts(db)
        assert counts["needs_review"] == 1
        assert counts["proposed"] == 0


class TestHoldoutAndConfidenceMetrics:
    def test_build_and_store_holdout_cases_excludes_candidate_texts(self, db):
        record_session_signal(
            db,
            {
                "session_id": "s-holdout-1",
                "session_date": "2099-01-01",
                "total_tokens": 100,
                "tool_call_count": 1,
                "correction_count": 1,
                "correction_snippets": ["fix branch naming", "add rollback guard"],
                "completion_flag": False,
                "skills_invoked": ["git-workflow"],
            },
        )
        tasks = build_and_store_holdout_cases(
            db,
            "git-workflow",
            days=30,
            limit=5,
            exclude_texts=["fix branch naming"],
        )
        assert "fix branch naming" not in tasks
        assert "add rollback guard" in tasks

    def test_record_eval_run_persists_row(self, db):
        row_id = record_eval_run(
            db,
            skill_name="git-workflow",
            anomaly_type="UNDERPERFORMING",
            status="accepted",
            self_play_token_delta=-0.2,
            self_play_quality_delta=0.5,
            holdout_quality_delta=0.1,
            holdout_pass=True,
            rubric_pass_rate_old=0.6,
            rubric_pass_rate_new=0.8,
            dual_judge_disagreement=False,
        )
        row = db.execute(
            "SELECT * FROM autoresearch_eval_runs WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert row is not None
        assert row["skill_name"] == "git-workflow"

    def test_operator_confidence_metrics_math(self, db):
        # Patches: 1 applied, 1 stable, 1 rolled_back
        p1 = record_autoresearch_patch(
            db,
            skill_name="s1",
            patch_type="patch",
            baseline_correction_rate=0.2,
            baseline_completion_rate=0.8,
        )
        p2 = record_autoresearch_patch(
            db,
            skill_name="s2",
            patch_type="patch",
            baseline_correction_rate=0.2,
            baseline_completion_rate=0.8,
        )
        p3 = record_autoresearch_patch(
            db,
            skill_name="s3",
            patch_type="patch",
            baseline_correction_rate=0.2,
            baseline_completion_rate=0.8,
        )
        db.execute("UPDATE autoresearch_patches SET status = 'stable' WHERE id = ?", (p2,))
        db.execute("UPDATE autoresearch_patches SET status = 'rolled_back' WHERE id = ?", (p3,))
        db.commit()

        # Memory outcomes: 1 applied, 1 failed
        m1 = upsert_memory_update_proposal(
            db,
            target="memory",
            action="replace",
            old_text="old",
            new_content="new",
            reason="r",
            confidence=0.9,
            evidence_count=2,
            apply_delay_hours=0,
        )
        m2 = upsert_memory_update_proposal(
            db,
            target="memory",
            action="remove",
            old_text="x",
            new_content="",
            reason="r",
            confidence=0.9,
            evidence_count=2,
            apply_delay_hours=0,
        )
        update_memory_update_status(db, m1, "applied", set_applied_at=True)
        update_memory_update_status(db, m2, "failed", error="boom")

        record_eval_run(
            db,
            skill_name="s1",
            anomaly_type="UNDERPERFORMING",
            status="accepted",
            self_play_token_delta=-0.1,
            self_play_quality_delta=0.2,
            holdout_quality_delta=0.05,
            holdout_pass=True,
            rubric_pass_rate_old=0.5,
            rubric_pass_rate_new=0.7,
            dual_judge_disagreement=False,
        )
        record_eval_run(
            db,
            skill_name="s2",
            anomaly_type="UNDERPERFORMING",
            status="rejected",
            self_play_token_delta=0.1,
            self_play_quality_delta=-0.2,
            holdout_quality_delta=-0.1,
            holdout_pass=False,
            rubric_pass_rate_old=0.5,
            rubric_pass_rate_new=0.4,
            dual_judge_disagreement=False,
        )

        metrics = get_operator_confidence_metrics(db, days=30)
        assert metrics["patch_stability_ratio"] == pytest.approx(0.5)  # stable/(stable+rolled_back)
        assert metrics["memory_precision_proxy"] == pytest.approx(0.5)  # 1 / (1+1)
        assert metrics["holdout_pass_rate"] == pytest.approx(0.5)
