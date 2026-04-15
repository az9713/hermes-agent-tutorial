"""
Tests for cron/autoresearch/regression_watch.py.

What we're testing
──────────────────
check_regressions() reads applied autoresearch patches, compares current
correction_rate to baseline, and rolls back or marks stable. These tests verify:

1. A skill whose correction_rate rose above ROLLBACK_THRESHOLD is rolled back.
2. After rollback, SKILL.md contains old_string again (new_string removed).
3. After rollback, SKILL_HISTORY.md has an [autoresearch: regression-watch] entry.
4. DB status updated to "rolled_back" after rollback.
5. A skill with stable metrics gets status "stable" in DB.
6. A skill with in-session patches since autoresearch is flagged "needs_review".
7. A needs_review skill is NOT rolled back (its SKILL.md unchanged).
8. No health data for a skill → silently skipped (no crash).
9. No applied patches → empty result list.
10. patches_since_ts filter works (only examines recent patches).
11. Rollback fails gracefully if new_string no longer in SKILL.md.

Why these tests matter
──────────────────────
Regression watch is the auto-recovery mechanism for bad autoresearch patches.
The "only rollback if sole writer" rule (in-session check) is the most subtle
safety guard — a false positive here would revert a skill that was already
manually fixed. These tests lock in both the rollback condition and the
causation-ambiguity guard.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cron.autoresearch.regression_watch import (
    ROLLBACK_THRESHOLD,
    check_regressions,
    _count_in_session_patches_since,
)
from cron.autoresearch.skill_metrics import (
    open_db,
    record_autoresearch_patch,
    upsert_skill_health,
    get_applied_patches,
)
from cron.autoresearch.applier import HISTORY_HEADER_RE


# ── Helpers ───────────────────────────────────────────────────────────────────

OLD_STRING = "Always do the right thing."
NEW_STRING = "Always do the right thing — specifically: X."

SKILL_CONTENT_AFTER_PATCH = f"""\
# my-skill

## Rules
- {NEW_STRING}
- Never skip verification.
"""

def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def make_skill_dir(tmp_path: Path, skill_name: str = "my-skill") -> Path:
    skill_dir = tmp_path / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_CONTENT_AFTER_PATCH, encoding="utf-8")
    return skill_dir


def insert_patch_record(
    conn,
    skill_name: str = "my-skill",
    baseline_correction_rate: float = 0.20,
    old_str: str = OLD_STRING,
    new_str: str = NEW_STRING,
    hours_ago: float = 25.0,
) -> int:
    """Insert a row into autoresearch_patches with a past applied_at timestamp."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    from cron.autoresearch.skill_metrics import record_autoresearch_patch as _rec
    # Manually insert with custom timestamp
    cursor = conn.execute(
        """
        INSERT INTO autoresearch_patches
            (skill_name, patch_applied_at, patch_type,
             baseline_tokens, baseline_correction_rate, baseline_completion_rate,
             old_string, new_string, status)
        VALUES (?, ?, 'patch', 0, ?, 0.8, ?, ?, 'applied')
        """,
        (skill_name, ts_iso, baseline_correction_rate, old_str, new_str),
    )
    conn.commit()
    return cursor.lastrowid


def set_current_health(
    conn,
    skill_name: str,
    correction_rate: float,
    invocation_count: int = 5,
):
    upsert_skill_health(
        conn, skill_name, today_utc(),
        invocation_count=invocation_count,
        avg_tokens=1000.0,
        correction_rate=correction_rate,
        completion_rate=0.80,
    )


def write_history_entry(skill_dir: Path, source: str, hours_ago: float = 0.5):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    history_path = skill_dir / "SKILL_HISTORY.md"
    entry = (
        f"\n## {ts_str} — patch [{source}]\n"
        f"**Reason:** test\n**File:** SKILL.md\n"
        f"\n### Old\n```text\nold\n```\n\n### New\n```text\nnew\n```\n"
    )
    existing = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    history_path.write_text(existing + entry, encoding="utf-8")


# ── Tests: rollback triggered ─────────────────────────────────────────────────

class TestRollbackTriggered:
    def test_bad_delta_triggers_rollback(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.20)
        # Current rate is 0.20 + ROLLBACK_THRESHOLD + 0.01 = way above threshold
        set_current_health(conn, "my-skill", correction_rate=0.20 + ROLLBACK_THRESHOLD + 0.01)

        results = check_regressions(conn, tmp_path)

        assert len(results) == 1
        assert results[0]["status"] == "rolled_back"
        conn.close()

    def test_skill_md_restored_after_rollback(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        skill_dir = make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.10)
        set_current_health(conn, "my-skill", correction_rate=0.50)

        check_regressions(conn, tmp_path)

        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert OLD_STRING in content
        assert NEW_STRING not in content
        conn.close()

    def test_history_entry_after_rollback(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        skill_dir = make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.10)
        set_current_health(conn, "my-skill", correction_rate=0.50)

        check_regressions(conn, tmp_path)

        history = (skill_dir / "SKILL_HISTORY.md").read_text(encoding="utf-8")
        assert "autoresearch: regression-watch" in history
        conn.close()

    def test_db_status_rolled_back(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        patch_id = insert_patch_record(conn, baseline_correction_rate=0.10)
        set_current_health(conn, "my-skill", correction_rate=0.50)

        check_regressions(conn, tmp_path)

        rows = conn.execute(
            "SELECT status FROM autoresearch_patches WHERE id=?", (patch_id,)
        ).fetchone()
        assert rows["status"] == "rolled_back"
        conn.close()

    def test_delta_in_result(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.20)
        set_current_health(conn, "my-skill", correction_rate=0.50)

        results = check_regressions(conn, tmp_path)

        assert results[0]["correction_rate_delta"] == pytest.approx(0.30, abs=0.01)
        conn.close()


# ── Tests: stable ─────────────────────────────────────────────────────────────

class TestStable:
    def test_small_delta_marked_stable(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.20)
        set_current_health(conn, "my-skill", correction_rate=0.22)  # delta=0.02 < 0.15

        results = check_regressions(conn, tmp_path)

        assert results[0]["status"] == "stable"
        conn.close()

    def test_stable_skill_md_unchanged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        skill_dir = make_skill_dir(tmp_path)
        original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        insert_patch_record(conn, baseline_correction_rate=0.20)
        set_current_health(conn, "my-skill", correction_rate=0.22)

        check_regressions(conn, tmp_path)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
        conn.close()

    def test_improved_metrics_marked_stable(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.40)
        set_current_health(conn, "my-skill", correction_rate=0.10)  # improved

        results = check_regressions(conn, tmp_path)

        assert results[0]["status"] == "stable"
        conn.close()

    def test_db_status_stable(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        patch_id = insert_patch_record(conn, baseline_correction_rate=0.20)
        set_current_health(conn, "my-skill", correction_rate=0.22)

        check_regressions(conn, tmp_path)

        row = conn.execute(
            "SELECT status FROM autoresearch_patches WHERE id=?", (patch_id,)
        ).fetchone()
        assert row["status"] == "stable"
        conn.close()


# ── Tests: needs_review (in-session patches since autoresearch) ───────────────

class TestNeedsReview:
    def test_in_session_patch_since_causes_needs_review(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        skill_dir = make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.10, hours_ago=25)
        set_current_health(conn, "my-skill", correction_rate=0.50)
        # in-session patch happened AFTER the autoresearch patch (1h ago)
        write_history_entry(skill_dir, "in-session", hours_ago=1.0)

        results = check_regressions(conn, tmp_path)

        assert results[0]["status"] == "needs_review"
        conn.close()

    def test_needs_review_skill_md_not_rolled_back(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        skill_dir = make_skill_dir(tmp_path)
        original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        insert_patch_record(conn, baseline_correction_rate=0.10, hours_ago=25)
        set_current_health(conn, "my-skill", correction_rate=0.50)
        write_history_entry(skill_dir, "in-session", hours_ago=1.0)

        check_regressions(conn, tmp_path)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
        conn.close()

    def test_autoresearch_entry_does_not_cause_needs_review(self, tmp_path):
        """[autoresearch] entries after the patch should not count."""
        conn = open_db(tmp_path / "metrics.db")
        skill_dir = make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.20, hours_ago=25)
        set_current_health(conn, "my-skill", correction_rate=0.22)
        write_history_entry(skill_dir, "autoresearch", hours_ago=1.0)

        results = check_regressions(conn, tmp_path)

        assert results[0]["status"] == "stable"
        conn.close()


# ── Tests: edge cases ─────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_no_applied_patches_returns_empty(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        results = check_regressions(conn, tmp_path)
        assert results == []
        conn.close()

    def test_no_health_data_skipped_silently(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        insert_patch_record(conn, baseline_correction_rate=0.20)
        # No health rows inserted — current_rate will be None

        results = check_regressions(conn, tmp_path)

        assert results == []  # skipped, not errored
        conn.close()

    def test_rollback_fails_gracefully_if_new_string_gone(self, tmp_path):
        """new_string already removed from SKILL.md → no crash."""
        conn = open_db(tmp_path / "metrics.db")
        skill_dir = make_skill_dir(tmp_path)
        # Overwrite skill with original content (new_string not present)
        (skill_dir / "SKILL.md").write_text(
            f"# my-skill\n\n## Rules\n- {OLD_STRING}\n", encoding="utf-8"
        )
        insert_patch_record(conn, baseline_correction_rate=0.10)
        set_current_health(conn, "my-skill", correction_rate=0.50)

        results = check_regressions(conn, tmp_path)

        # Should not crash; status may be needs_review (rollback failed)
        assert len(results) == 1
        assert results[0]["status"] in {"rolled_back", "needs_review"}
        conn.close()

    def test_patches_since_ts_filter(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        make_skill_dir(tmp_path)
        # Insert an old patch (50h ago)
        insert_patch_record(conn, baseline_correction_rate=0.10, hours_ago=50)
        set_current_health(conn, "my-skill", correction_rate=0.50)

        # Filter to only patches from the last 24h → old patch excluded
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        results = check_regressions(conn, tmp_path, patches_since_ts=cutoff)

        assert results == []
        conn.close()


# ── Tests: _count_in_session_patches_since ────────────────────────────────────

class TestCountInSessionPatchesSince:
    def _make_dir(self, tmp_path):
        d = tmp_path / "skills" / "x"
        d.mkdir(parents=True)
        return d

    def test_no_history_returns_zero(self, tmp_path):
        skill_dir = self._make_dir(tmp_path)
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        assert _count_in_session_patches_since(skill_dir, since) == 0

    def test_counts_in_session_after_cutoff(self, tmp_path):
        skill_dir = self._make_dir(tmp_path)
        write_history_entry(skill_dir, "in-session", hours_ago=1.0)
        since = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert _count_in_session_patches_since(skill_dir, since) == 1

    def test_ignores_in_session_before_cutoff(self, tmp_path):
        skill_dir = self._make_dir(tmp_path)
        write_history_entry(skill_dir, "in-session", hours_ago=30.0)
        since = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert _count_in_session_patches_since(skill_dir, since) == 0

    def test_ignores_autoresearch_entries(self, tmp_path):
        skill_dir = self._make_dir(tmp_path)
        write_history_entry(skill_dir, "autoresearch", hours_ago=1.0)
        since = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert _count_in_session_patches_since(skill_dir, since) == 0
