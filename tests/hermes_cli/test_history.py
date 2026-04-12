"""Tests for the `hermes skills history` CLI path.

Covers:
    Class 1 — TestParseAllHistoryRecords     pure string parsing helper
    Class 2 — TestDoHistoryTableView         default table output
    Class 3 — TestDoHistoryDetailView        --detail N diff output
    Class 4 — TestDoHistoryErrorPaths        missing skill / history / unparseable
    Class 5 — TestHistoryCommandDispatch     routing via skills_command
    Class 6 — TestHistoryArgparse            argparse registration in main.py
"""

from argparse import Namespace
from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from hermes_cli.skills_hub import (
    _parse_all_history_records,
    do_history,
    skills_command,
)
from tools.skill_manager_tool import (
    _create_skill,
    _patch_skill,
    _append_skill_history,
    SKILL_HISTORY_FILE,
)

# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------

from tests.tools.test_skill_manager_tool import (
    _skill_dir,
    VALID_SKILL_CONTENT,
)


def _capture_console():
    """Return (sink, console) so Rich output can be inspected as a string."""
    sink = StringIO()
    return sink, Console(file=sink, force_terminal=False, color_system=None)


# Module-level history record strings matching the format _append_skill_history writes.
# The em-dash is U+2014 as required by the header format.

_PATCH_RECORD = (
    "\n## 2026-04-11T14:32:05Z \u2014 patch\n"
    "**Reason:** Initial improvement\n"
    "**File:** SKILL.md\n"
    "\n### Old\n```text\nStep 1: Do the thing.\n```\n"
    "\n### New\n```text\nStep 1: Do the new thing.\n```\n"
)

_ROLLBACK_RECORD = (
    "\n## 2026-04-11T14:35:00Z \u2014 rollback\n"
    "**Reason:** Rolled back via CLI\n"
    "**File:** SKILL.md\n"
    "\n### Old\n```text\nStep 1: Do the new thing.\n```\n"
    "\n### New\n```text\nStep 1: Do the thing.\n```\n"
)

_EDIT_RECORD = (
    "\n## 2026-04-11T15:00:00Z \u2014 edit\n"
    "**Reason:** Full rewrite\n"
    "**File:** SKILL.md\n"
    "\n### Old\n```text\nOriginal content.\n```\n"
    "\n### New\n```text\nRewritten content.\n```\n"
)


# ===========================================================================
# Class 1 — Pure parser unit tests
# ===========================================================================

class TestParseAllHistoryRecords:
    """Unit tests for _parse_all_history_records.

    Unlike _parse_last_history_record, this parser:
    - Returns ALL records (oldest first)
    - Includes rollback records — the full audit trail
    - Returns [] on empty / unparseable input
    """

    def test_empty_text_returns_empty_list(self):
        assert _parse_all_history_records("") == []

    def test_single_patch_record(self):
        records = _parse_all_history_records(_PATCH_RECORD)
        assert len(records) == 1
        r = records[0]
        assert r["timestamp"] == "2026-04-11T14:32:05Z"
        assert r["action"] == "patch"
        assert r["reason"] == "Initial improvement"
        assert r["file_path"] == "SKILL.md"
        assert r["old_text"] == "Step 1: Do the thing."
        assert r["new_text"] == "Step 1: Do the new thing."

    def test_multiple_records_preserves_order(self):
        # Two records joined — oldest (patch) should be list[0]
        text = _PATCH_RECORD + _ROLLBACK_RECORD
        records = _parse_all_history_records(text)
        assert len(records) == 2
        assert records[0]["action"] == "patch"
        assert records[1]["action"] == "rollback"

    def test_rollback_record_included(self):
        # _parse_all_history_records includes rollback records.
        # This is the key difference from _parse_last_history_record which skips them.
        records = _parse_all_history_records(_ROLLBACK_RECORD)
        assert len(records) == 1
        assert records[0]["action"] == "rollback"

    def test_malformed_record_skipped(self):
        # A block without a valid ## timestamp — action header is silently skipped.
        malformed = "\nThis is not a valid header\n**Reason:** orphan\n"
        text = malformed + _PATCH_RECORD
        records = _parse_all_history_records(text)
        # Only the valid patch record should appear
        assert len(records) == 1
        assert records[0]["action"] == "patch"


# ===========================================================================
# Class 2 — Table view (default output)
# ===========================================================================

class TestDoHistoryTableView:
    """Integration tests for do_history default table output.

    All tests use _skill_dir + _create_skill + _patch_skill to produce real
    SKILL_HISTORY.md files on disk, then inspect the Rich table output.
    """

    def test_single_patch_shows_table(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="test reason")

            sink, console = _capture_console()
            do_history("my-skill", console=console)
            output = sink.getvalue()

        assert "my-skill" in output
        assert "patch" in output
        assert "test reason" in output
        assert "SKILL.md" in output

    def test_multiple_patches_numbered(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="first patch")
            _patch_skill("my-skill", "Step 1: Do the new thing.", "Step 1: Do the final thing.", reason="second patch")

            sink, console = _capture_console()
            do_history("my-skill", console=console)
            output = sink.getvalue()

        assert "1" in output
        assert "2" in output
        assert "first patch" in output
        assert "second patch" in output

    def test_rollback_record_in_table(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="patch it")

            from hermes_cli.skills_hub import do_rollback
            sink_r, console_r = _capture_console()
            do_rollback("my-skill", skip_confirm=True, console=console_r)

            sink, console = _capture_console()
            do_history("my-skill", console=console)
            output = sink.getvalue()

        assert "rollback" in output

    def test_shows_record_count(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="r1")
            _patch_skill("my-skill", "Step 1: Do the new thing.", "Step 1: Final.", reason="r2")

            sink, console = _capture_console()
            do_history("my-skill", console=console)
            output = sink.getvalue()

        assert "2 record(s)" in output

    def test_hint_about_detail_flag(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="r")

            sink, console = _capture_console()
            do_history("my-skill", console=console)
            output = sink.getvalue()

        assert "--detail" in output


# ===========================================================================
# Class 3 — Detail view (--detail N)
# ===========================================================================

class TestDoHistoryDetailView:
    """Tests for do_history with detail=N showing the full diff for one record."""

    def test_detail_shows_diff(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="improve step 1")

            sink, console = _capture_console()
            do_history("my-skill", detail=1, console=console)
            output = sink.getvalue()

        assert "Record #1" in output
        assert "patch" in output
        # Diff should contain the added/removed content
        assert "Do the new thing" in output

    def test_detail_out_of_range(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="r")

            sink, console = _capture_console()
            do_history("my-skill", detail=99, console=console)
            output = sink.getvalue()

        assert "does not exist" in output
        assert "99" in output

    def test_detail_zero_is_invalid(self, tmp_path):
        # Records are 1-indexed; 0 is not a valid record number.
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            _patch_skill("my-skill", "Step 1: Do the thing.", "Step 1: Do the new thing.", reason="r")

            sink, console = _capture_console()
            do_history("my-skill", detail=0, console=console)
            output = sink.getvalue()

        assert "does not exist" in output

    def test_detail_identity_diff(self, tmp_path):
        # When old_text == new_text the unified_diff is empty → "No visible diff".
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            skill_dir = tmp_path / "my-skill"
            _append_skill_history(
                skill_dir=skill_dir,
                action="patch",
                reason="no-op patch",
                file_path="SKILL.md",
                old_text="Same content.",
                new_text="Same content.",
            )

            sink, console = _capture_console()
            do_history("my-skill", detail=1, console=console)
            output = sink.getvalue()

        assert "No visible diff" in output


# ===========================================================================
# Class 4 — Error paths
# ===========================================================================

class TestDoHistoryErrorPaths:
    """Tests for every early-return branch in do_history."""

    def test_unknown_skill_prints_error(self, tmp_path):
        with _skill_dir(tmp_path):
            sink, console = _capture_console()
            do_history("nonexistent-skill", console=console)
            output = sink.getvalue()

        assert "not found" in output.lower()

    def test_no_history_file_prints_warning(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            # Do NOT patch — no SKILL_HISTORY.md exists.

            sink, console = _capture_console()
            do_history("my-skill", console=console)
            output = sink.getvalue()

        assert "No history found" in output

    def test_empty_history_file_prints_warning(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("my-skill", VALID_SKILL_CONTENT)
            # Write an empty SKILL_HISTORY.md — _parse_all_history_records returns [].
            (tmp_path / "my-skill" / SKILL_HISTORY_FILE).write_text("", encoding="utf-8")

            sink, console = _capture_console()
            do_history("my-skill", console=console)
            output = sink.getvalue()

        assert "No parseable records" in output


# ===========================================================================
# Class 5 — Routing via skills_command
# ===========================================================================

class TestHistoryCommandDispatch:
    """Tests that skills_command() routes 'history' to do_history with correct args."""

    def test_skills_command_routes_history(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr("hermes_cli.skills_hub.do_history", spy)
        args = Namespace(skills_action="history", name="test-skill", detail=None)
        skills_command(args)
        spy.assert_called_once_with("test-skill", detail=None)

    def test_skills_command_routes_history_with_detail(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr("hermes_cli.skills_hub.do_history", spy)
        args = Namespace(skills_action="history", name="test-skill", detail=3)
        skills_command(args)
        spy.assert_called_once_with("test-skill", detail=3)


# ===========================================================================
# Class 6 — Argparse registration
# ===========================================================================

class TestHistoryArgparse:
    """Tests that 'hermes skills history' is correctly wired in main.py.

    Uses the real ArgumentParser built inside main() — not a replica — so any
    rename or removal in main.py will fail these tests immediately.

    The spy on hermes_cli.skills_hub.skills_command is reachable because
    cmd_skills (inside main()) does a fresh 'from hermes_cli.skills_hub import
    skills_command' at call time, resolving from the module object where the
    patch lives.
    """

    def _run_skills_cmd(self, argv, monkeypatch):
        """Invoke main() with argv; return the Namespace the spy captured."""
        captured = {}
        monkeypatch.setattr("sys.argv", ["hermes"] + argv)
        monkeypatch.setattr(
            "hermes_cli.skills_hub.skills_command",
            lambda args: captured.update(vars(args)),
        )
        from hermes_cli.main import main
        main()
        return captured

    def test_history_subparser_registered(self, monkeypatch):
        result = self._run_skills_cmd(["skills", "history", "my-skill"], monkeypatch)
        assert result["skills_action"] == "history"
        assert result["name"] == "my-skill"
        assert result["detail"] is None

    def test_history_detail_flag(self, monkeypatch):
        result = self._run_skills_cmd(
            ["skills", "history", "my-skill", "--detail", "3"], monkeypatch
        )
        assert result["detail"] == 3

    def test_history_missing_name_errors(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["hermes", "skills", "history"])
        monkeypatch.setattr(
            "hermes_cli.skills_hub.skills_command",
            lambda args: None,
        )
        from hermes_cli.main import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_history_detail_requires_int(self, monkeypatch):
        # argparse type=int should reject non-integer values with SystemExit(2).
        monkeypatch.setattr("sys.argv", ["hermes", "skills", "history", "my-skill", "--detail", "abc"])
        monkeypatch.setattr(
            "hermes_cli.skills_hub.skills_command",
            lambda args: None,
        )
        from hermes_cli.main import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
