"""
Tests for the source tag added to _append_skill_history().

What we're testing
──────────────────
Slice 1 made one change to tools/skill_manager_tool.py: the `source`
parameter on `_append_skill_history()`.  These tests verify:

1. Default source ("in-session") is written when no source is given.
   This proves backward compatibility — all existing callers that don't
   pass `source` continue to produce valid history records.

2. Explicit source ("autoresearch") is written when passed.
   This is what the Stage 3 applier will pass.

3. Compound source ("autoresearch: regression-watch") is written.
   This is what the regression watch will pass.

4. Multiple records accumulate correctly (append-only).

5. The source tag appears in the section header, not somewhere else,
   so `hermes skills history` can parse it.

Why these tests matter
──────────────────────
The source tag is the ONLY change to existing production code in Stage 1.
All coexistence guarantees (knowing which system wrote what entry) depend on
this tag being written correctly and parseable from the history file.
"""

import re
from pathlib import Path

import pytest

from tools.skill_manager_tool import _append_skill_history, SKILL_HISTORY_FILE


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def skill_dir(tmp_path) -> Path:
    """A temporary directory standing in for a real skill directory."""
    d = tmp_path / "test-skill"
    d.mkdir()
    return d


# ── Helper ───────────────────────────────────────────────────────────────────

def read_history(skill_dir: Path) -> str:
    return (skill_dir / SKILL_HISTORY_FILE).read_text(encoding="utf-8")


# ── Tests: default source ─────────────────────────────────────────────────────

class TestDefaultSource:
    def test_default_source_is_in_session(self, skill_dir):
        """Calling _append_skill_history without source writes [in-session]."""
        _append_skill_history(
            skill_dir=skill_dir,
            action="patch",
            reason="fixed a bug",
            file_path="SKILL.md",
            old_text="old content",
            new_text="new content",
        )
        history = read_history(skill_dir)
        assert "[in-session]" in history

    def test_default_source_in_header_line(self, skill_dir):
        """The source tag appears on the ## header line, not in the body."""
        _append_skill_history(
            skill_dir=skill_dir,
            action="edit",
            reason="major rewrite",
            file_path="SKILL.md",
            old_text="old",
            new_text="new",
        )
        history = read_history(skill_dir)
        # Header line pattern: ## <timestamp> — <action> [<source>]
        header_lines = [l for l in history.splitlines() if l.startswith("## ")]
        assert len(header_lines) == 1
        assert "[in-session]" in header_lines[0]

    def test_reason_and_file_still_present(self, skill_dir):
        """Adding source tag doesn't remove reason or file fields."""
        _append_skill_history(
            skill_dir=skill_dir,
            action="patch",
            reason="my reason",
            file_path="SKILL.md",
            old_text="a",
            new_text="b",
        )
        history = read_history(skill_dir)
        assert "**Reason:** my reason" in history
        assert "**File:** SKILL.md" in history


# ── Tests: explicit sources ───────────────────────────────────────────────────

class TestExplicitSource:
    def test_autoresearch_source(self, skill_dir):
        """Passing source='autoresearch' writes [autoresearch] in header."""
        _append_skill_history(
            skill_dir=skill_dir,
            action="patch",
            reason="correction_rate 0.41",
            file_path="SKILL.md",
            old_text="old",
            new_text="new",
            source="autoresearch",
        )
        history = read_history(skill_dir)
        header_lines = [l for l in history.splitlines() if l.startswith("## ")]
        assert "[autoresearch]" in header_lines[0]
        assert "[in-session]" not in header_lines[0]

    def test_regression_watch_source(self, skill_dir):
        """Passing compound source 'autoresearch: regression-watch' is written verbatim."""
        _append_skill_history(
            skill_dir=skill_dir,
            action="rollback",
            reason="correction_rate rose +18%",
            file_path="SKILL.md",
            old_text="patched",
            new_text="original",
            source="autoresearch: regression-watch",
        )
        history = read_history(skill_dir)
        header_lines = [l for l in history.splitlines() if l.startswith("## ")]
        assert "[autoresearch: regression-watch]" in header_lines[0]

    def test_custom_source_written_verbatim(self, skill_dir):
        """Any arbitrary source string is written without modification."""
        _append_skill_history(
            skill_dir=skill_dir,
            action="patch",
            reason="test",
            file_path="SKILL.md",
            old_text="x",
            new_text="y",
            source="manual-cli",
        )
        history = read_history(skill_dir)
        assert "[manual-cli]" in history


# ── Tests: multiple records ───────────────────────────────────────────────────

class TestMultipleRecords:
    def test_records_accumulate_append_only(self, skill_dir):
        """Each call appends a new record; prior records are not modified."""
        _append_skill_history(
            skill_dir, "patch", "first", "SKILL.md", "a", "b"
        )
        _append_skill_history(
            skill_dir, "patch", "second", "SKILL.md", "b", "c",
            source="autoresearch"
        )
        history = read_history(skill_dir)
        header_lines = [l for l in history.splitlines() if l.startswith("## ")]
        assert len(header_lines) == 2

    def test_mixed_sources_in_same_file(self, skill_dir):
        """In-session and autoresearch entries coexist correctly."""
        _append_skill_history(
            skill_dir, "patch", "in-session fix", "SKILL.md", "a", "b",
            source="in-session"
        )
        _append_skill_history(
            skill_dir, "patch", "nightly improvement", "SKILL.md", "b", "c",
            source="autoresearch"
        )
        history = read_history(skill_dir)
        assert "[in-session]" in history
        assert "[autoresearch]" in history

    def test_sources_on_correct_headers(self, skill_dir):
        """Each source tag appears on its own header, not another record's."""
        _append_skill_history(
            skill_dir, "patch", "r1", "SKILL.md", "a", "b", source="in-session"
        )
        _append_skill_history(
            skill_dir, "patch", "r2", "SKILL.md", "b", "c", source="autoresearch"
        )
        headers = [l for l in read_history(skill_dir).splitlines() if l.startswith("## ")]
        assert "[in-session]" in headers[0]
        assert "[autoresearch]" in headers[1]


# ── Tests: header format parseable by existing CLI ────────────────────────────

class TestHeaderFormat:
    """The existing `hermes skills history` parser splits on ## <timestamp> — <action>.
    Ensure the source tag doesn't break that pattern."""

    HEADER_RE = re.compile(
        r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) — (\w+) \[(.+)\]$"
    )

    def test_header_is_parseable(self, skill_dir):
        """Header line matches the expected regex: ## TS — action [source]."""
        _append_skill_history(
            skill_dir, "patch", "reason", "SKILL.md", "old", "new",
            source="autoresearch"
        )
        history = read_history(skill_dir)
        header = next(l for l in history.splitlines() if l.startswith("## "))
        match = self.HEADER_RE.match(header)
        assert match is not None, f"Header did not match expected pattern: {header!r}"
        timestamp, action, source = match.groups()
        assert action == "patch"
        assert source == "autoresearch"

    def test_timestamp_is_utc_iso(self, skill_dir):
        """Timestamp in header is a valid UTC ISO-8601 string."""
        from datetime import datetime, timezone
        _append_skill_history(
            skill_dir, "edit", "reason", "SKILL.md", "old", "new"
        )
        history = read_history(skill_dir)
        header = next(l for l in history.splitlines() if l.startswith("## "))
        match = self.HEADER_RE.match(header)
        assert match, f"Header didn't parse: {header!r}"
        ts_str = match.group(1)
        # Should parse as a valid datetime
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
        assert dt is not None
