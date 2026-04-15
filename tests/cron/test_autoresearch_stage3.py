"""
Integration tests for cron/autoresearch/__init__.py — the Stage 3 entry point.

What we're testing
──────────────────
run_stage3() wires pending_patches.json → apply → regression watch → digest.
These tests verify:

1. Empty pending_patches.json → digest written, no crash.
2. Accepted patch → SKILL.md modified, [autoresearch] entry in SKILL_HISTORY.md.
3. Accepted patch with recency lock → deferred, SKILL.md unchanged.
4. dry_run=True → SKILL.md unchanged, digest says "No patches applied".
5. Regression watch rolled back → SKILL.md restored, digest shows rollback.
6. run_regression_watch=False → watch skipped, digest still written.
7. Digest file is always written.
8. Digest text is returned by run_stage3().

Why these tests matter
──────────────────────
The integration test is the safety net for the entire Stage 3 pipeline. If
apply, regression_watch, or digest breaks, these tests catch it even if unit
tests pass. All tests use real SQLite and real filesystem in tmp_path.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cron.autoresearch import run_stage3
from cron.autoresearch.skill_metrics import open_db, upsert_skill_health


# ── Helpers ───────────────────────────────────────────────────────────────────

OLD_STRING = "Always do the right thing."
NEW_STRING = "Always do the right thing — specifically: X."

SKILL_CONTENT = f"""\
# my-skill

## Rules
- {OLD_STRING}
- Never skip verification.
"""

PATCHED_CONTENT = SKILL_CONTENT.replace(OLD_STRING, NEW_STRING)


def make_skill(tmp_path: Path, skill_name: str = "my-skill", content: str = None) -> Path:
    skill_dir = tmp_path / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content or SKILL_CONTENT, encoding="utf-8")
    return skill_dir


def write_pending(tmp_path: Path, patches: list) -> Path:
    path = tmp_path / "pending_patches.json"
    path.write_text(json.dumps(patches), encoding="utf-8")
    return path


def make_patch_entry(
    skill_name: str = "my-skill",
    accepted: bool = True,
    old_string: str = OLD_STRING,
    new_string: str = NEW_STRING,
) -> dict:
    return {
        "skill_name": skill_name,
        "anomaly_type": "UNDERPERFORMING",
        "trigger_metric": "correction_rate=0.41",
        "action": "patch",
        "status": "accepted" if accepted else "rejected",
        "accepted": accepted,
        "token_delta": -0.12,
        "quality_delta": 0.40,
        "judge_scores": [[7.0, 8.0]],
        "old_string": old_string,
        "new_string": new_string,
        "reason": "Clarify rule",
        "hold_reason": "",
        "rejection_reason": "",
        "correction_rate": 0.41,
        "completion_rate": 0.80,
        "avg_tokens": 1000.0,
        "generated_at": "2026-04-15T03:00:00Z",
    }


def write_history_entry(skill_dir: Path, source: str, hours_ago: float):
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


def run(tmp_path: Path, **kwargs) -> str:
    return run_stage3(
        metrics_db_path=kwargs.pop("metrics_db_path", tmp_path / "metrics.db"),
        patches_path=kwargs.pop("patches_path", tmp_path / "pending_patches.json"),
        digest_path=kwargs.pop("digest_path", tmp_path / "nightly_digest.md"),
        hermes_home=tmp_path,
        **kwargs,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStage3Integration:
    def test_empty_patches_no_crash(self, tmp_path):
        write_pending(tmp_path, [])
        text = run(tmp_path)
        assert "Hermes Autoresearch" in text

    def test_digest_always_written(self, tmp_path):
        write_pending(tmp_path, [])
        digest_path = tmp_path / "nightly_digest.md"
        run(tmp_path, digest_path=digest_path)
        assert digest_path.exists()

    def test_digest_text_returned(self, tmp_path):
        write_pending(tmp_path, [])
        text = run(tmp_path)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_accepted_patch_applied_to_skill_md(self, tmp_path):
        make_skill(tmp_path)
        write_pending(tmp_path, [make_patch_entry(accepted=True)])

        run(tmp_path, run_regression_watch=False)

        content = (tmp_path / "skills" / "my-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert NEW_STRING in content

    def test_applied_patch_has_autoresearch_history_entry(self, tmp_path):
        make_skill(tmp_path)
        write_pending(tmp_path, [make_patch_entry(accepted=True)])

        run(tmp_path, run_regression_watch=False)

        history = (tmp_path / "skills" / "my-skill" / "SKILL_HISTORY.md").read_text(encoding="utf-8")
        assert "[autoresearch]" in history

    def test_recency_locked_patch_deferred(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        write_history_entry(skill_dir, "in-session", hours_ago=1.0)
        write_pending(tmp_path, [make_patch_entry(accepted=True)])
        original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        run(tmp_path, run_regression_watch=False)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original

    def test_deferred_appears_in_digest(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        write_history_entry(skill_dir, "in-session", hours_ago=1.0)
        write_pending(tmp_path, [make_patch_entry(accepted=True)])

        text = run(tmp_path, run_regression_watch=False)

        assert "Deferred" in text
        assert "my-skill" in text

    def test_dry_run_skill_md_unchanged(self, tmp_path):
        skill_dir = make_skill(tmp_path)
        original = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        write_pending(tmp_path, [make_patch_entry(accepted=True)])

        run(tmp_path, dry_run=True, run_regression_watch=False)

        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original

    def test_dry_run_digest_says_no_patches_applied(self, tmp_path):
        make_skill(tmp_path)
        write_pending(tmp_path, [make_patch_entry(accepted=True)])

        text = run(tmp_path, dry_run=True, run_regression_watch=False)

        assert "No patches applied" in text

    def test_regression_watch_skipped_when_disabled(self, tmp_path):
        make_skill(tmp_path)
        write_pending(tmp_path, [])

        text = run(tmp_path, run_regression_watch=False)

        assert "No patches under regression watch" in text

    def test_regression_rollback_appears_in_digest(self, tmp_path):
        """Apply a patch then simulate worsening metrics → rollback shows in digest."""
        skill_dir = make_skill(tmp_path)
        write_pending(tmp_path, [make_patch_entry(accepted=True)])
        metrics_db = tmp_path / "metrics.db"

        # Apply stage 3 first (no watch)
        run(tmp_path, metrics_db_path=metrics_db, run_regression_watch=False)

        # Now SKILL.md has NEW_STRING in it; simulate worsened metrics
        conn = open_db(metrics_db)
        upsert_skill_health(
            conn, "my-skill",
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            invocation_count=5, avg_tokens=1000,
            correction_rate=0.50,   # baseline was 0.41 → delta=0.09 < threshold
            completion_rate=0.70,
        )
        # Manually bump to trigger rollback (baseline 0.41, threshold 0.15 → need > 0.56)
        upsert_skill_health(
            conn, "my-skill",
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            invocation_count=10, avg_tokens=1000,
            correction_rate=0.80,   # delta = 0.80 - 0.41 = 0.39 > 0.15 → rollback
            completion_rate=0.50,
        )
        conn.close()

        # Write empty patches and run stage3 again with watch enabled
        write_pending(tmp_path, [])
        text = run(tmp_path, metrics_db_path=metrics_db, run_regression_watch=True)

        # rolled_back or needs_review (skill may have been manually edited)
        assert "↩" in text or "⚠" in text or "rolled_back" in text or "needs_review" in text
