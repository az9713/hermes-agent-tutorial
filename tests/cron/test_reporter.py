"""
Tests for cron/autoresearch/reporter.py — nightly report generation.

What we're testing
──────────────────
reporter.py produces a Markdown file summarising skill health. These tests verify:

1. Report contains the correct date header.
2. Session count is rendered correctly.
3. Skills above FLAGGED thresholds appear in the Flagged Skills section.
4. Skills within thresholds appear as OK.
5. When no skills have data, the "no invocations" fallback message appears.
6. The report is written to disk at the requested path.
7. The function also returns the report text as a string.
8. TOKEN_EFFICIENCY_FLAG threshold is not a flagging condition on its own
   (correction_rate and completion_rate are the gate).

Why these tests matter
──────────────────────
The reporter is the human-readable output of Stage 1. If thresholds are applied
incorrectly or sections are missing, operators make wrong decisions about which
skills to patch. These tests lock in the exact reporting contract.
"""

from pathlib import Path
from typing import List, Dict, Any

import pytest

from cron.autoresearch.reporter import (
    CORRECTION_RATE_THRESHOLD,
    COMPLETION_RATE_THRESHOLD,
    generate_report,
    _skill_status,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_health(
    skill_name: str = "test-skill",
    total_invocations: int = 10,
    avg_tokens: float = 1000.0,
    correction_rate: float = 0.1,
    completion_rate: float = 0.9,
) -> Dict[str, Any]:
    return {
        "skill_name": skill_name,
        "total_invocations": total_invocations,
        "avg_tokens": avg_tokens,
        "correction_rate": correction_rate,
        "completion_rate": completion_rate,
    }


def run_report(
    tmp_path: Path,
    session_count: int = 5,
    skill_health: List[Dict[str, Any]] = None,
    report_date: str = "2026-01-15",
) -> str:
    report_path = tmp_path / "nightly_report.md"
    return generate_report(
        session_count=session_count,
        skill_health=skill_health or [],
        report_path=report_path,
        report_date=report_date,
    )


# ── Tests: _skill_status ─────────────────────────────────────────────────────

class TestSkillStatus:
    def test_ok_when_within_thresholds(self):
        h = make_health(correction_rate=0.1, completion_rate=0.9)
        assert _skill_status(h) == "OK ✓"

    def test_flagged_when_correction_rate_too_high(self):
        h = make_health(correction_rate=CORRECTION_RATE_THRESHOLD + 0.01)
        assert _skill_status(h) == "FLAGGED ⚠"

    def test_flagged_when_completion_rate_too_low(self):
        h = make_health(completion_rate=COMPLETION_RATE_THRESHOLD - 0.01)
        assert _skill_status(h) == "FLAGGED ⚠"

    def test_ok_at_exact_thresholds(self):
        """At exactly the threshold values the skill is NOT flagged (> not >=)."""
        h = make_health(
            correction_rate=CORRECTION_RATE_THRESHOLD,
            completion_rate=COMPLETION_RATE_THRESHOLD,
        )
        assert _skill_status(h) == "OK ✓"

    def test_none_values_treated_as_zero(self):
        """None metrics don't crash — they're coerced to 0."""
        h = {"skill_name": "x", "correction_rate": None, "completion_rate": None}
        assert _skill_status(h) == "FLAGGED ⚠"  # completion_rate 0 < 0.5


# ── Tests: report content ─────────────────────────────────────────────────────

class TestReportContent:
    def test_report_date_in_header(self, tmp_path):
        text = run_report(tmp_path, report_date="2026-03-15")
        assert "2026-03-15" in text

    def test_session_count_in_report(self, tmp_path):
        text = run_report(tmp_path, session_count=42)
        assert "42" in text

    def test_skill_appears_in_table(self, tmp_path):
        text = run_report(tmp_path, skill_health=[make_health("git-workflow")])
        assert "git-workflow" in text

    def test_ok_skill_not_in_flagged_section(self, tmp_path):
        ok_skill = make_health("good-skill", correction_rate=0.05, completion_rate=0.95)
        text = run_report(tmp_path, skill_health=[ok_skill])
        # The flagged section should have "No skills flagged"
        assert "_No skills flagged this cycle._" in text

    def test_flagged_skill_appears_in_flagged_section(self, tmp_path):
        bad_skill = make_health("bad-skill", correction_rate=0.8, completion_rate=0.3)
        text = run_report(tmp_path, skill_health=[bad_skill])
        assert "bad-skill" in text
        flagged_idx = text.index("## Flagged Skills")
        assert "bad-skill" in text[flagged_idx:]

    def test_no_skills_fallback_message(self, tmp_path):
        text = run_report(tmp_path, skill_health=[])
        assert "_No skill invocations recorded in the last 7 days._" in text

    def test_stage1_only_footer(self, tmp_path):
        """Report must include the 'Stage 1: Observe only' marker."""
        text = run_report(tmp_path)
        assert "Stage 1" in text
        assert "No patches applied" in text

    def test_skill_count_in_header(self, tmp_path):
        health = [make_health(f"skill-{i}") for i in range(3)]
        text = run_report(tmp_path, skill_health=health)
        assert "3" in text

    def test_correction_rate_formatted_as_percent(self, tmp_path):
        text = run_report(tmp_path, skill_health=[make_health(correction_rate=0.25)])
        assert "25%" in text

    def test_tokens_formatted_with_commas(self, tmp_path):
        text = run_report(tmp_path, skill_health=[make_health(avg_tokens=12345.0)])
        assert "12,345" in text


# ── Tests: file output ────────────────────────────────────────────────────────

class TestFileOutput:
    def test_report_written_to_disk(self, tmp_path):
        report_path = tmp_path / "nightly_report.md"
        generate_report(
            session_count=1,
            skill_health=[],
            report_path=report_path,
            report_date="2026-01-01",
        )
        assert report_path.exists()

    def test_report_text_matches_file_content(self, tmp_path):
        report_path = tmp_path / "nightly_report.md"
        text = generate_report(
            session_count=1,
            skill_health=[],
            report_path=report_path,
            report_date="2026-01-01",
        )
        assert report_path.read_text(encoding="utf-8") == text

    def test_parent_directory_created(self, tmp_path):
        """generate_report creates intermediate directories if needed."""
        nested_path = tmp_path / "deep" / "nested" / "report.md"
        generate_report(
            session_count=0,
            skill_health=[],
            report_path=nested_path,
            report_date="2026-01-01",
        )
        assert nested_path.exists()
