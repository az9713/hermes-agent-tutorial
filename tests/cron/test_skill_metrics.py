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
from datetime import date, timedelta
from pathlib import Path

import pytest

from cron.autoresearch.skill_metrics import (
    already_extracted,
    compute_and_store_skill_health,
    get_skill_health_summary,
    open_db,
    record_session_signal,
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
