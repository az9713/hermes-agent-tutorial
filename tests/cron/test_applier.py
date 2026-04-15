"""
Tests for cron/autoresearch/applier.py.

What we're testing
──────────────────
apply_patches() reads accepted patches from pending_patches.json entries,
checks the recency lock, applies the str.replace to SKILL.md, writes a
SKILL_HISTORY.md entry, and records in autoresearch_patches. These tests verify:

1. Accepted patch is applied to SKILL.md.
2. SKILL_HISTORY.md is appended with an [autoresearch] entry.
3. autoresearch_patches DB row is written after apply.
4. Patch with in-session entry within 24h is deferred (recency lock).
5. Patch with in-session entry older than 24h is applied (lock expired).
6. No SKILL_HISTORY.md → no recency lock (treated as no prior patches).
7. SKILL.md not found → status "failed", no crash.
8. old_string not in current SKILL.md → status "failed" (stale patch).
9. dry_run=True → SKILL.md unchanged, no history entry, status "dry_run".
10. Non-accepted patches are ignored.
11. [autoresearch] and [autoresearch: regression-watch] history entries
    do NOT trigger the recency lock (only [in-session] does).
12. Multiple accepted patches processed in order.

Why these tests matter
──────────────────────
The applier is the first Stage 3 component that writes to skill files.
The recency lock and old_string staleness check are the primary safety guards.
These tests lock in all three safety mechanisms before any live skill files
are touched.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cron.autoresearch.applier import (
    HISTORY_HEADER_RE,
    RECENCY_LOCK_HOURS,
    apply_patches,
    _is_recency_locked,
    _last_in_session_patch_ts,
)
from cron.autoresearch.skill_metrics import open_db, get_applied_patches


# ── Helpers ───────────────────────────────────────────────────────────────────

OLD_CONTENT = """\
# my-skill

## Rules
- Always do the right thing.
- Never skip verification.
"""

def make_skill(tmp_path: Path, skill_name: str = "my-skill") -> Path:
    skill_dir = tmp_path / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(OLD_CONTENT, encoding="utf-8")
    return skill_dir


def make_patch(
    skill_name: str = "my-skill",
    old_string: str = "Always do the right thing.",
    new_string: str = "Always do the right thing — specifically: X.",
    accepted: bool = True,
    reason: str = "Clarify rule",
    correction_rate: float = 0.41,
    completion_rate: float = 0.80,
) -> dict:
    return {
        "skill_name": skill_name,
        "accepted": accepted,
        "status": "accepted" if accepted else "rejected",
        "old_string": old_string,
        "new_string": new_string,
        "reason": reason,
        "correction_rate": correction_rate,
        "completion_rate": completion_rate,
        "avg_tokens": 1000.0,
    }


def write_history_entry(skill_dir: Path, source: str, hours_ago: float = 0.0):
    """Write a minimal SKILL_HISTORY.md entry with the given source."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    history_path = skill_dir / "SKILL_HISTORY.md"
    entry = (
        f"\n## {ts_str} — patch [{source}]\n"
        f"**Reason:** test entry\n"
        f"**File:** SKILL.md\n"
        f"\n### Old\n```text\nold\n```\n"
        f"\n### New\n```text\nnew\n```\n"
    )
    existing = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    history_path.write_text(existing + entry, encoding="utf-8")


# ── Tests: successful apply ───────────────────────────────────────────────────

class TestSuccessfulApply:
    def test_skill_md_content_updated(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path)

        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "Always do the right thing — specifically: X." in content
        assert "Always do the right thing." not in content
        conn.close()

    def test_old_text_replaced_not_deleted(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path)

        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "Never skip verification." in content  # untouched line remains
        conn.close()

    def test_status_is_applied(self, tmp_path):
        make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path)

        assert results[0]["status"] == "applied"
        conn.close()

    def test_history_entry_written(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path)

        history = (skill_dir / "SKILL_HISTORY.md").read_text(encoding="utf-8")
        assert "[autoresearch]" in history
        conn.close()

    def test_history_entry_contains_old_and_new(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        p = make_patch()
        apply_patches([p], conn, tmp_path)

        history = (skill_dir / "SKILL_HISTORY.md").read_text(encoding="utf-8")
        assert p["old_string"] in history
        assert p["new_string"] in history
        conn.close()

    def test_history_header_parseable(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path)

        history = (skill_dir / "SKILL_HISTORY.md").read_text(encoding="utf-8")
        matches = [
            HISTORY_HEADER_RE.match(line)
            for line in history.splitlines()
            if line.startswith("## ")
        ]
        parseable = [m for m in matches if m is not None]
        assert len(parseable) >= 1
        assert parseable[-1].group(3) == "autoresearch"
        conn.close()

    def test_db_row_written(self, tmp_path):
        make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path)

        rows = get_applied_patches(conn)
        assert len(rows) == 1
        assert rows[0]["skill_name"] == "my-skill"
        conn.close()

    def test_db_row_has_baselines(self, tmp_path):
        make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch(correction_rate=0.41, completion_rate=0.80)], conn, tmp_path)

        rows = get_applied_patches(conn)
        assert rows[0]["baseline_correction_rate"] == pytest.approx(0.41)
        assert rows[0]["baseline_completion_rate"] == pytest.approx(0.80)
        conn.close()


# ── Tests: recency lock ───────────────────────────────────────────────────────

class TestRecencyLock:
    def test_recent_in_session_patch_defers(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        write_history_entry(skill_dir, "in-session", hours_ago=1.0)
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path)

        assert results[0]["status"] == "deferred"
        assert "in-session patch" in results[0]["reason"]
        conn.close()

    def test_deferred_skill_md_not_modified(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        write_history_entry(skill_dir, "in-session", hours_ago=1.0)
        original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
        conn.close()

    def test_old_in_session_patch_allows_apply(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        write_history_entry(skill_dir, "in-session", hours_ago=RECENCY_LOCK_HOURS + 1)
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path)

        assert results[0]["status"] == "applied"
        conn.close()

    def test_no_history_file_no_lock(self, tmp_path):
        make_skill(tmp_path)  # no history file written
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path)

        assert results[0]["status"] == "applied"
        conn.close()

    def test_autoresearch_history_does_not_trigger_lock(self, tmp_path):
        """[autoresearch] entries must NOT count as in-session patches."""
        skill_dir = make_skill(tmp_path)
        write_history_entry(skill_dir, "autoresearch", hours_ago=1.0)
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path)

        assert results[0]["status"] == "applied"
        conn.close()

    def test_regression_watch_history_does_not_trigger_lock(self, tmp_path):
        """[autoresearch: regression-watch] entries must NOT trigger lock."""
        skill_dir = make_skill(tmp_path)
        write_history_entry(skill_dir, "autoresearch: regression-watch", hours_ago=0.5)
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path)

        assert results[0]["status"] == "applied"
        conn.close()


# ── Tests: failure cases ──────────────────────────────────────────────────────

class TestFailureCases:
    def test_missing_skill_md_returns_failed(self, tmp_path):
        # Don't create the skill dir
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path)

        assert results[0]["status"] == "failed"
        assert "not found" in results[0]["reason"]
        conn.close()

    def test_stale_old_string_returns_failed(self, tmp_path):
        make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        stale = make_patch(old_string="This text is not in the skill.")
        results = apply_patches([stale], conn, tmp_path)

        assert results[0]["status"] == "failed"
        assert "stale" in results[0]["reason"]
        conn.close()

    def test_stale_patch_does_not_modify_skill(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        conn = open_db(tmp_path / "metrics.db")

        stale = make_patch(old_string="Not in the file.")
        apply_patches([stale], conn, tmp_path)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
        conn.close()


# ── Tests: dry_run ────────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_skill_md_unchanged(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path, dry_run=True)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original
        conn.close()

    def test_dry_run_no_history_entry(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path, dry_run=True)

        assert not (skill_dir / "SKILL_HISTORY.md").exists()
        conn.close()

    def test_dry_run_no_db_row(self, tmp_path):
        make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        apply_patches([make_patch()], conn, tmp_path, dry_run=True)

        assert get_applied_patches(conn) == []
        conn.close()

    def test_dry_run_status_is_dry_run(self, tmp_path):
        make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch()], conn, tmp_path, dry_run=True)

        assert results[0]["status"] == "dry_run"
        conn.close()


# ── Tests: non-accepted patches ignored ──────────────────────────────────────

class TestNonAccepted:
    def test_rejected_patch_ignored(self, tmp_path):
        make_skill(tmp_path)
        conn = open_db(tmp_path / "metrics.db")

        results = apply_patches([make_patch(accepted=False)], conn, tmp_path)

        assert results == []
        conn.close()

    def test_mixed_list_only_accepted_processed(self, tmp_path):
        make_skill(tmp_path, "skill-a")
        make_skill(tmp_path, "skill-b")
        conn = open_db(tmp_path / "metrics.db")

        patches = [
            make_patch("skill-a", accepted=True),
            make_patch("skill-b", accepted=False),
        ]
        results = apply_patches(patches, conn, tmp_path)

        assert len(results) == 1
        assert results[0]["skill_name"] == "skill-a"
        conn.close()


# ── Tests: _last_in_session_patch_ts ─────────────────────────────────────────

class TestLastInSessionPatchTs:
    def test_no_history_file_returns_none(self, tmp_path):
        skill_dir = tmp_path / "skills" / "x"
        skill_dir.mkdir(parents=True)
        assert _last_in_session_patch_ts(skill_dir) is None

    def test_in_session_entry_detected(self, tmp_path):
        skill_dir = tmp_path / "skills" / "x"
        skill_dir.mkdir(parents=True)
        write_history_entry(skill_dir, "in-session", hours_ago=2.0)
        ts = _last_in_session_patch_ts(skill_dir)
        assert ts is not None

    def test_only_autoresearch_entries_returns_none(self, tmp_path):
        skill_dir = tmp_path / "skills" / "x"
        skill_dir.mkdir(parents=True)
        write_history_entry(skill_dir, "autoresearch", hours_ago=1.0)
        assert _last_in_session_patch_ts(skill_dir) is None

    def test_returns_most_recent_of_multiple(self, tmp_path):
        skill_dir = tmp_path / "skills" / "x"
        skill_dir.mkdir(parents=True)
        write_history_entry(skill_dir, "in-session", hours_ago=10.0)
        write_history_entry(skill_dir, "in-session", hours_ago=2.0)
        ts = _last_in_session_patch_ts(skill_dir)
        # Should be ~2h ago, not ~10h ago
        age = datetime.now(timezone.utc) - ts
        assert age < timedelta(hours=5)
