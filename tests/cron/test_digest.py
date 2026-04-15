"""
Tests for cron/autoresearch/digest.py.

What we're testing
──────────────────
generate_digest() formats a Markdown nightly digest from apply_results,
watch_results, and pending_patches. These tests verify:

1. Report date appears in header.
2. Applied patches appear in Applied section.
3. Deferred patches appear in Deferred section with reason.
4. Rejected patches appear in Rejected section with token/quality deltas.
5. Stable regression watch result appears with ✓ symbol.
6. Rolled-back watch result appears with ↩ symbol.
7. needs_review watch result appears in Needs your attention section.
8. Failed apply appears in Needs your attention section.
9. Empty apply_results produces fallback message.
10. Empty watch_results produces fallback message.
11. Nothing needing attention → "Nothing needs your attention."
12. File is written to disk.
13. Returned text matches file content.
14. Parent directories created automatically.

Why these tests matter
──────────────────────
The digest is what operators read each morning to understand what the
autoresearch loop did. Mis-routed results (e.g. a rollback appearing as
stable, or an attention item silently dropped) would cause operators to
miss problems. These tests lock in the routing logic.
"""

from pathlib import Path

import pytest

from cron.autoresearch.digest import generate_digest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_apply(skill_name: str, status: str, reason: str = "Clarify rule") -> dict:
    return {
        "skill_name": skill_name,
        "status": status,
        "reason": reason,
        "old_string": "old",
        "new_string": "new",
    }


def make_watch(skill_name: str, status: str, delta: float = 0.02, reason: str = "") -> dict:
    return {
        "skill_name": skill_name,
        "patch_id": 1,
        "status": status,
        "correction_rate_delta": delta,
        "reason": reason or f"correction_rate delta={delta:+.0%}",
    }


def make_pending(
    skill_name: str,
    status: str,
    token_delta: float = -0.10,
    quality_delta: float = 0.5,
    rejection_reason: str = "",
) -> dict:
    return {
        "skill_name": skill_name,
        "status": status,
        "accepted": status == "accepted",
        "token_delta": token_delta,
        "quality_delta": quality_delta,
        "rejection_reason": rejection_reason,
    }


def run_digest(
    tmp_path: Path,
    apply_results=None,
    watch_results=None,
    pending_patches=None,
    report_date: str = "2026-04-15",
) -> str:
    path = tmp_path / "nightly_digest.md"
    return generate_digest(
        apply_results=apply_results or [],
        watch_results=watch_results or [],
        pending_patches=pending_patches or [],
        report_path=path,
        report_date=report_date,
    )


# ── Tests: header ─────────────────────────────────────────────────────────────

class TestHeader:
    def test_date_in_header(self, tmp_path):
        text = run_digest(tmp_path, report_date="2026-04-15")
        assert "2026-04-15" in text

    def test_autoresearch_title_in_header(self, tmp_path):
        text = run_digest(tmp_path)
        assert "Hermes Autoresearch" in text


# ── Tests: Applied section ────────────────────────────────────────────────────

class TestAppliedSection:
    def test_applied_skill_in_applied_section(self, tmp_path):
        text = run_digest(tmp_path, apply_results=[make_apply("git-workflow", "applied")])
        applied_idx = text.index("## Applied")
        assert "git-workflow" in text[applied_idx:]

    def test_applied_reason_in_section(self, tmp_path):
        text = run_digest(tmp_path, apply_results=[
            make_apply("git-workflow", "applied", reason="Clarify branch naming")
        ])
        assert "Clarify branch naming" in text

    def test_empty_applied_fallback(self, tmp_path):
        text = run_digest(tmp_path, apply_results=[])
        assert "No patches applied" in text


# ── Tests: Deferred section ───────────────────────────────────────────────────

class TestDeferredSection:
    def test_deferred_skill_in_deferred_section(self, tmp_path):
        text = run_digest(tmp_path, apply_results=[
            make_apply("web-search", "deferred", reason="in-session patch within 24h")
        ])
        deferred_idx = text.index("## Deferred")
        assert "web-search" in text[deferred_idx:]

    def test_applied_skill_not_in_deferred_section(self, tmp_path):
        text = run_digest(tmp_path, apply_results=[
            make_apply("git-workflow", "applied"),
            make_apply("web-search", "deferred"),
        ])
        deferred_idx = text.index("## Deferred")
        applied_idx = text.index("## Applied")
        deferred_section = text[deferred_idx:applied_idx] if applied_idx < deferred_idx else text[deferred_idx:]
        assert "git-workflow" not in deferred_section

    def test_empty_deferred_fallback(self, tmp_path):
        text = run_digest(tmp_path)
        assert "No patches deferred" in text


# ── Tests: Rejected section ───────────────────────────────────────────────────

class TestRejectedSection:
    def test_rejected_patch_in_section(self, tmp_path):
        text = run_digest(tmp_path, pending_patches=[
            make_pending("code-review", "rejected", token_delta=0.05, quality_delta=-0.8,
                        rejection_reason="token_delta >= 0")
        ])
        rejected_idx = text.index("## Rejected")
        assert "code-review" in text[rejected_idx:]

    def test_token_delta_in_rejected_section(self, tmp_path):
        text = run_digest(tmp_path, pending_patches=[
            make_pending("code-review", "rejected", token_delta=0.05)
        ])
        assert "+5%" in text or "5%" in text

    def test_accepted_patch_not_in_rejected_section(self, tmp_path):
        text = run_digest(tmp_path, pending_patches=[
            make_pending("git-workflow", "accepted"),
            make_pending("code-review", "rejected"),
        ])
        rejected_idx = text.index("## Rejected")
        assert "git-workflow" not in text[rejected_idx:rejected_idx + 200]

    def test_empty_rejected_fallback(self, tmp_path):
        text = run_digest(tmp_path)
        assert "No patches rejected" in text


# ── Tests: Regression watch section ──────────────────────────────────────────

class TestRegressionWatchSection:
    def test_stable_shows_checkmark(self, tmp_path):
        text = run_digest(tmp_path, watch_results=[
            make_watch("git-workflow", "stable")
        ])
        watch_idx = text.index("## Regression watch")
        assert "✓" in text[watch_idx:]

    def test_rolled_back_shows_rollback_symbol(self, tmp_path):
        text = run_digest(tmp_path, watch_results=[
            make_watch("bad-skill", "rolled_back", delta=0.30)
        ])
        watch_idx = text.index("## Regression watch")
        assert "↩" in text[watch_idx:]

    def test_needs_review_shows_warning_symbol(self, tmp_path):
        text = run_digest(tmp_path, watch_results=[
            make_watch("ambiguous-skill", "needs_review")
        ])
        watch_idx = text.index("## Regression watch")
        assert "⚠" in text[watch_idx:]

    def test_skill_name_in_watch_section(self, tmp_path):
        text = run_digest(tmp_path, watch_results=[
            make_watch("git-workflow", "stable")
        ])
        watch_idx = text.index("## Regression watch")
        assert "git-workflow" in text[watch_idx:]

    def test_empty_watch_fallback(self, tmp_path):
        text = run_digest(tmp_path, watch_results=[])
        assert "No patches under regression watch" in text


# ── Tests: Needs attention section ────────────────────────────────────────────

class TestNeedsAttention:
    def test_needs_review_in_attention_section(self, tmp_path):
        text = run_digest(tmp_path, watch_results=[
            make_watch("ambiguous-skill", "needs_review",
                      reason="in-session patches since autoresearch")
        ])
        attention_idx = text.index("## Needs your attention")
        assert "ambiguous-skill" in text[attention_idx:]

    def test_failed_apply_in_attention_section(self, tmp_path):
        text = run_digest(tmp_path, apply_results=[
            make_apply("broken-skill", "failed", reason="SKILL.md not found")
        ])
        attention_idx = text.index("## Needs your attention")
        assert "broken-skill" in text[attention_idx:]

    def test_nothing_needing_attention_fallback(self, tmp_path):
        text = run_digest(tmp_path,
            apply_results=[make_apply("git-workflow", "applied")],
            watch_results=[make_watch("git-workflow", "stable")],
        )
        assert "Nothing needs your attention" in text

    def test_stable_not_in_attention_section(self, tmp_path):
        text = run_digest(tmp_path, watch_results=[
            make_watch("good-skill", "stable")
        ])
        attention_idx = text.index("## Needs your attention")
        # Nothing needing attention
        assert "good-skill" not in text[attention_idx:]


# ── Tests: file output ────────────────────────────────────────────────────────

class TestFileOutput:
    def test_file_written_to_disk(self, tmp_path):
        path = tmp_path / "nightly_digest.md"
        generate_digest([], [], [], report_path=path, report_date="2026-01-01")
        assert path.exists()

    def test_returned_text_matches_file(self, tmp_path):
        path = tmp_path / "nightly_digest.md"
        text = generate_digest([], [], [], report_path=path, report_date="2026-01-01")
        assert path.read_text(encoding="utf-8") == text

    def test_parent_directories_created(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "digest.md"
        generate_digest([], [], [], report_path=nested, report_date="2026-01-01")
        assert nested.exists()
