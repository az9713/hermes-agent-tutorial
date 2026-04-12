"""Tests for the rollback CLI path.

Covers:
    Class 1 — TestParseLastHistoryRecord    pure string parsing helper
    Class 2 — TestDoRollbackHappyPath       end-to-end success cases
    Class 3 — TestDoRollbackErrorPaths      missing skill / history / malformed
    Class 4 — TestDoRollbackClearsPromptCache  lazy-import cache-clear behavior
    Class 5 — TestSkillsCommandRollbackDispatch  routing via skills_command
    Class 6 — TestRollbackArgparse          argparse registration in main.py
"""

from argparse import Namespace
from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from hermes_cli.skills_hub import (
    _parse_last_history_record,
    do_rollback,
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

# Re-use the patching context-manager and baseline skill content from the
# skill_manager_tool test module rather than duplicating them.
from tests.tools.test_skill_manager_tool import (
    _skill_dir,
    VALID_SKILL_CONTENT,
)


def _capture_console():
    """Return (sink, console) so Rich output can be inspected as a string."""
    sink = StringIO()
    return sink, Console(file=sink, force_terminal=False, color_system=None)


# Well-formed history records matching the exact format _append_skill_history
# writes.  The em-dash (U+2014) and spacing are significant — they are what
# the regex in _parse_last_history_record matches.
_PATCH_RECORD = (
    "\n## 2026-04-11T14:32:05Z \u2014 patch\n"
    "**Reason:** Initial improvement\n"
    "**File:** SKILL.md\n"
    "\n### Old\n```text\nStep 1: Do the thing.\n```\n"
    "\n### New\n```text\nStep 1: Do the new thing.\n```\n"
)

_ROLLBACK_RECORD = (
    "\n## 2026-04-11T15:00:00Z \u2014 rollback\n"
    "**Reason:** Rolled back via CLI\n"
    "**File:** SKILL.md\n"
    "\n### Old\n```text\nStep 1: Do the new thing.\n```\n"
    "\n### New\n```text\nStep 1: Do the thing.\n```\n"
)


# ---------------------------------------------------------------------------
# Class 1 — _parse_last_history_record  (unit, no filesystem)
# ---------------------------------------------------------------------------

class TestParseLastHistoryRecord:
    """Pure string-parsing tests.  No disk access — history text is inline."""

    def test_empty_text_returns_none_tuple(self):
        assert _parse_last_history_record("") == (None, None, None)

    def test_single_patch_record_extracted(self):
        file_path, old_text, new_text = _parse_last_history_record(_PATCH_RECORD)
        assert file_path == "SKILL.md"
        assert old_text == "Step 1: Do the thing."
        assert new_text == "Step 1: Do the new thing."

    def test_multiple_records_returns_most_recent(self):
        # An 'edit' appended after the patch should be returned (newer timestamp).
        edit_record = (
            "\n## 2026-04-11T14:33:00Z \u2014 edit\n"
            "**Reason:** Full rewrite\n"
            "**File:** SKILL.md\n"
            "\n### Old\n```text\nStep 1: Do the new thing.\n```\n"
            "\n### New\n```text\nStep 1: Do the final thing.\n```\n"
        )
        history = _PATCH_RECORD + edit_record
        _fp, old_text, new_text = _parse_last_history_record(history)
        assert old_text == "Step 1: Do the new thing."
        assert new_text == "Step 1: Do the final thing."

    def test_rollback_record_is_skipped(self):
        # patch → rollback.  Parser must skip the rollback and return the patch.
        history = _PATCH_RECORD + _ROLLBACK_RECORD
        _fp, old_text, new_text = _parse_last_history_record(history)
        assert old_text == "Step 1: Do the thing."
        assert new_text == "Step 1: Do the new thing."

    def test_all_rollback_records_returns_none(self):
        assert _parse_last_history_record(_ROLLBACK_RECORD) == (None, None, None)

    def test_edit_action_also_returned(self):
        # The action keyword 'edit' should parse identically to 'patch'.
        edit_record = _PATCH_RECORD.replace("\u2014 patch", "\u2014 edit")
        file_path, old_text, _nt = _parse_last_history_record(edit_record)
        assert file_path == "SKILL.md"
        assert old_text == "Step 1: Do the thing."

    def test_malformed_record_without_old_block_returns_none(self):
        # Missing '### Old' fenced block — falls through to (None, None, None).
        bad_record = (
            "\n## 2026-04-11T14:32:05Z \u2014 patch\n"
            "**Reason:** Test\n"
            "**File:** SKILL.md\n"
            "\n### New\n```text\nsomething new\n```\n"
        )
        assert _parse_last_history_record(bad_record) == (None, None, None)


# ---------------------------------------------------------------------------
# Class 2 — do_rollback happy path  (functional, uses _skill_dir)
# ---------------------------------------------------------------------------

class TestDoRollbackHappyPath:
    """End-to-end tests that exercise do_rollback against a temp skill dir."""

    def test_patch_then_rollback_restores_original(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )

            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)

            content = (tmp_path / "test-skill" / "SKILL.md").read_text(encoding="utf-8")
            assert "Step 1: Do the thing." in content
            assert "Step 1: Do the new thing." not in content
            assert "Rolled back 'test-skill'" in sink.getvalue()

    def test_rollback_appends_rollback_record_to_history(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )
            _, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)

            history = (
                tmp_path / "test-skill" / SKILL_HISTORY_FILE
            ).read_text(encoding="utf-8")

            # Two records: original patch + rollback.
            assert history.count("\u2014 patch") == 1
            assert history.count("\u2014 rollback") == 1
            assert "Rolled back via CLI" in history

    def test_rollback_shows_unified_diff_preview(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )

            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)

            output = sink.getvalue()
            # Output contains the diff preview header at minimum.
            assert "Rollback preview" in output

    def test_rollback_with_identical_content_prints_no_visible_diff(self, tmp_path):
        """When old_text == new_text in the history, no diff lines exist."""
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            skill_dir = tmp_path / "test-skill"
            # Manually write an identity patch record.
            _append_skill_history(
                skill_dir=skill_dir,
                action="patch",
                reason="identity patch",
                file_path="SKILL.md",
                old_text="same content",
                new_text="same content",
            )
            # Align SKILL.md to the 'new' content so the diff is empty.
            (skill_dir / "SKILL.md").write_text(
                VALID_SKILL_CONTENT.replace("Step 1: Do the thing.", "same content"),
                encoding="utf-8",
            )

            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)

            assert "No visible diff" in sink.getvalue()

    def test_skip_confirm_false_with_y_restores(self, monkeypatch, tmp_path):
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )
            _, console = _capture_console()
            do_rollback("test-skill", skip_confirm=False, console=console)

            content = (tmp_path / "test-skill" / "SKILL.md").read_text(encoding="utf-8")
            assert "Step 1: Do the thing." in content

    def test_skip_confirm_false_with_n_cancels(self, monkeypatch, tmp_path):
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )
            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=False, console=console)

            # File should still contain the patched version.
            content = (tmp_path / "test-skill" / "SKILL.md").read_text(encoding="utf-8")
            assert "Step 1: Do the new thing." in content
            assert "Cancelled" in sink.getvalue()

    def test_confirm_eof_treated_as_no(self, monkeypatch, tmp_path):
        monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )
            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=False, console=console)

            content = (tmp_path / "test-skill" / "SKILL.md").read_text(encoding="utf-8")
            assert "Step 1: Do the new thing." in content
            assert "Cancelled" in sink.getvalue()


# ---------------------------------------------------------------------------
# Class 3 — do_rollback error paths
# ---------------------------------------------------------------------------

class TestDoRollbackErrorPaths:

    def test_unknown_skill_prints_error(self, tmp_path):
        with _skill_dir(tmp_path):
            sink, console = _capture_console()
            do_rollback("nonexistent-skill", skip_confirm=True, console=console)
            assert "not found" in sink.getvalue().lower()

    def test_skill_exists_but_no_history_prints_warning(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            # No patches — SKILL_HISTORY.md does not exist.
            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)
            assert "No history found" in sink.getvalue()

    def test_history_only_rollback_records_prints_no_restorable(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            skill_dir = tmp_path / "test-skill"
            # Write only a rollback record — nothing to restore to.
            (skill_dir / SKILL_HISTORY_FILE).write_text(_ROLLBACK_RECORD, encoding="utf-8")

            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)
            assert "No restorable record found" in sink.getvalue()

    def test_malformed_history_prints_no_restorable(self, tmp_path):
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            skill_dir = tmp_path / "test-skill"
            # Record with the '### New' block missing.
            bad_record = (
                "\n## 2026-04-11T14:32:05Z \u2014 patch\n"
                "**Reason:** Test\n"
                "**File:** SKILL.md\n"
                "\n### Old\n```text\nold content\n```\n"
            )
            (skill_dir / SKILL_HISTORY_FILE).write_text(bad_record, encoding="utf-8")

            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)
            assert "No restorable record found" in sink.getvalue()


# ---------------------------------------------------------------------------
# Class 4 — do_rollback clears the prompt cache
# ---------------------------------------------------------------------------

class TestDoRollbackClearsPromptCache:
    """Verify do_rollback calls clear_skills_system_prompt_cache(clear_snapshot=True).

    do_rollback imports the cache-clear function lazily inside its body:

        from agent.prompt_builder import clear_skills_system_prompt_cache
        clear_skills_system_prompt_cache(clear_snapshot=True)

    Patching agent.prompt_builder.clear_skills_system_prompt_cache before the
    call works because the lazy 'from ... import' resolves the name from the
    module object at call time, so it sees the patched binding.

    If do_rollback is ever refactored to import at module scope, the correct
    patch target becomes hermes_cli.skills_hub.clear_skills_system_prompt_cache.
    """

    def test_successful_rollback_clears_prompt_cache(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(
            "agent.prompt_builder.clear_skills_system_prompt_cache",
            lambda **kw: calls.append(kw),
        )
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )
            _, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)

        assert calls == [{"clear_snapshot": True}]

    def test_cache_clear_failure_is_swallowed(self, monkeypatch, tmp_path):
        """A RuntimeError from cache clear must not propagate to the caller."""
        monkeypatch.setattr(
            "agent.prompt_builder.clear_skills_system_prompt_cache",
            MagicMock(side_effect=RuntimeError("cache unavailable")),
        )
        with _skill_dir(tmp_path):
            _create_skill("test-skill", VALID_SKILL_CONTENT)
            _patch_skill(
                "test-skill",
                "Step 1: Do the thing.",
                "Step 1: Do the new thing.",
                reason="test",
            )
            sink, console = _capture_console()
            do_rollback("test-skill", skip_confirm=True, console=console)  # must not raise

        assert "Rolled back 'test-skill'" in sink.getvalue()


# ---------------------------------------------------------------------------
# Class 5 — skills_command rollback dispatch
# ---------------------------------------------------------------------------

class TestSkillsCommandRollbackDispatch:
    """Verify that skills_command routes skills_action='rollback' to do_rollback.

    We spy on hermes_cli.skills_hub.do_rollback so no filesystem access is
    needed.  The Namespace mirrors what argparse produces after:
        hermes skills rollback <name> [--yes]
    """

    def test_skills_command_routes_rollback_with_yes_true(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr("hermes_cli.skills_hub.do_rollback", spy)

        args = Namespace(skills_action="rollback", name="test-skill", yes=True)
        skills_command(args)

        spy.assert_called_once_with("test-skill", skip_confirm=True)

    def test_skills_command_rollback_defaults_yes_false(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr("hermes_cli.skills_hub.do_rollback", spy)

        # 'yes' absent — getattr(args, "yes", False) must resolve to False.
        args = Namespace(skills_action="rollback", name="test-skill")
        skills_command(args)

        spy.assert_called_once_with("test-skill", skip_confirm=False)


# ---------------------------------------------------------------------------
# Class 6 — Argparse parser registration
# ---------------------------------------------------------------------------

class TestRollbackArgparse:
    """Verify the rollback subparser is wired correctly in hermes_cli/main.py.

    Strategy: monkeypatch sys.argv and hermes_cli.skills_hub.skills_command,
    then call hermes_cli.main.main().  main() builds the real ArgumentParser,
    parses the patched sys.argv, and routes to cmd_skills (defined inside
    main()), which in turn does:

        from hermes_cli.skills_hub import skills_command
        skills_command(args)

    Because that import is lazy (resolved at call time), the monkeypatched
    binding is picked up.  The spy captures the parsed Namespace so we can
    assert on every attribute that argparse should have set.
    """

    def _run_skills_cmd(self, argv: list, monkeypatch) -> dict:
        """Invoke main() with argv; return the Namespace the spy captured."""
        captured: dict = {}
        monkeypatch.setattr("sys.argv", ["hermes"] + argv)
        monkeypatch.setattr(
            "hermes_cli.skills_hub.skills_command",
            lambda args: captured.update(vars(args)),
        )
        from hermes_cli.main import main
        main()
        return captured

    def test_rollback_subparser_registered(self, monkeypatch):
        captured = self._run_skills_cmd(
            ["skills", "rollback", "my-skill"], monkeypatch
        )
        assert captured.get("skills_action") == "rollback"
        assert captured.get("name") == "my-skill"
        assert captured.get("yes") is False

    def test_rollback_yes_flag_long(self, monkeypatch):
        captured = self._run_skills_cmd(
            ["skills", "rollback", "my-skill", "--yes"], monkeypatch
        )
        assert captured.get("yes") is True

    def test_rollback_yes_flag_short(self, monkeypatch):
        captured = self._run_skills_cmd(
            ["skills", "rollback", "my-skill", "-y"], monkeypatch
        )
        assert captured.get("yes") is True

    def test_rollback_missing_name_errors(self, monkeypatch):
        """Missing positional 'name' arg causes argparse to SystemExit(2)."""
        monkeypatch.setattr("sys.argv", ["hermes", "skills", "rollback"])
        with pytest.raises(SystemExit) as exc_info:
            from hermes_cli.main import main
            main()
        assert exc_info.value.code == 2
