"""
Tests for hermes_cli/autoresearch.py — CLI handler for `hermes autoresearch`.

What we're testing
──────────────────
autoresearch_command() dispatches to subcommand handlers. These tests verify:

1. `run` — calls run_full_loop, prints digest, returns 0.
2. `run --dry-run` — passes dry_run=True to run_full_loop.
3. `run` with exception — prints error, returns 1.
4. `status` — prints enabled/schedule/last_run from config + state.
5. `status` with error state — last error shown.
6. `schedule <expr>` — saves schedule to config.
7. `schedule` without expr — returns 1.
8. `patches` — no pending_patches.json → prints "No pending_patches.json".
9. `patches` — empty patches → prints "empty".
10. `patches` — with patches → prints skill names.
11. `enable` / `disable` — toggle enabled in config.
12. Unknown subcommand → returns 1.

Why these tests matter
──────────────────────
The CLI is the operator's interface: if `status` shows stale data, or `run`
swallows errors silently, operators can't tell what the loop is doing. These
tests lock in the output contracts and return codes.
"""

import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.autoresearch import autoresearch_command


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_args(**kwargs) -> types.SimpleNamespace:
    defaults = {
        "autoresearch_cmd": None,
        "dry_run": False,
        "expr": None,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


AR_CONFIG = {
    "autoresearch": {
        "enabled": True,
        "schedule": "0 2 * * *",
        "dry_run": False,
        "deliver": ["slack"],
    }
}

OK_STATE = {
    "last_run_at": "2026-04-15T02:00:00+00:00",
    "last_status": "ok",
    "last_error": None,
}

ERROR_STATE = {
    "last_run_at": "2026-04-15T02:00:00+00:00",
    "last_status": "error",
    "last_error": "Stage 2 failed: DB gone",
}


# ── `run` subcommand ──────────────────────────────────────────────────────────

class TestRunCmd:
    def test_run_calls_full_loop_and_prints_digest(self, capsys):
        with patch("cron.autoresearch.runner.run_full_loop", return_value="# Digest\n\nAll good.") as mock_loop:
            rc = autoresearch_command(make_args(autoresearch_cmd="run"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "# Digest" in out
        mock_loop.assert_called_once_with(dry_run=False)

    def test_run_dry_run_passed_through(self, capsys):
        with patch("cron.autoresearch.runner.run_full_loop", return_value="# Digest") as mock_loop:
            autoresearch_command(make_args(autoresearch_cmd="run", dry_run=True))
        mock_loop.assert_called_once_with(dry_run=True)

    def test_run_exception_returns_1(self, capsys):
        with patch("cron.autoresearch.runner.run_full_loop", side_effect=RuntimeError("boom")):
            rc = autoresearch_command(make_args(autoresearch_cmd="run"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "boom" in err


# ── `status` subcommand ───────────────────────────────────────────────────────

class TestStatusCmd:
    def test_status_shows_schedule(self, capsys):
        with (
            patch("hermes_cli.autoresearch._load_config", return_value=AR_CONFIG),
            patch("hermes_cli.autoresearch._load_run_state", return_value=OK_STATE),
        ):
            rc = autoresearch_command(make_args(autoresearch_cmd="status"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 2 * * *" in out

    def test_status_shows_last_run(self, capsys):
        with (
            patch("hermes_cli.autoresearch._load_config", return_value=AR_CONFIG),
            patch("hermes_cli.autoresearch._load_run_state", return_value=OK_STATE),
        ):
            autoresearch_command(make_args(autoresearch_cmd="status"))
        out = capsys.readouterr().out
        assert "2026-04-15" in out

    def test_status_shows_error(self, capsys):
        with (
            patch("hermes_cli.autoresearch._load_config", return_value=AR_CONFIG),
            patch("hermes_cli.autoresearch._load_run_state", return_value=ERROR_STATE),
        ):
            autoresearch_command(make_args(autoresearch_cmd="status"))
        out = capsys.readouterr().out
        assert "Stage 2 failed" in out

    def test_status_never_run_shows_never(self, capsys):
        state = {"last_run_at": None, "last_status": None, "last_error": None}
        with (
            patch("hermes_cli.autoresearch._load_config", return_value=AR_CONFIG),
            patch("hermes_cli.autoresearch._load_run_state", return_value=state),
        ):
            autoresearch_command(make_args(autoresearch_cmd="status"))
        out = capsys.readouterr().out
        assert "never" in out

    def test_status_shows_memory_outcome_counts(self, capsys, tmp_path):
        with (
            patch("hermes_cli.autoresearch._load_config", return_value=AR_CONFIG),
            patch("hermes_cli.autoresearch._load_run_state", return_value=OK_STATE),
            patch(
                "hermes_cli.autoresearch._memory_update_counts",
                return_value={
                    "proposed": 1,
                    "pending_revalidation": 2,
                    "needs_review": 3,
                    "applied": 4,
                    "discarded": 5,
                    "failed": 6,
                },
            ),
            patch("hermes_cli.autoresearch._get_hermes_home", return_value=tmp_path),
        ):
            autoresearch_command(make_args(autoresearch_cmd="status"))
        out = capsys.readouterr().out
        assert "memory outcomes" in out
        assert "applied:             4" in out
        assert "discarded:           5" in out
        assert "failed:              6" in out

    def test_status_shows_operator_confidence_block(self, capsys, tmp_path):
        with (
            patch("hermes_cli.autoresearch._load_config", return_value=AR_CONFIG),
            patch("hermes_cli.autoresearch._load_run_state", return_value=OK_STATE),
            patch("hermes_cli.autoresearch._memory_update_counts", return_value={}),
            patch(
                "hermes_cli.autoresearch._operator_confidence_metrics",
                return_value={
                    "patch_stability_ratio": 0.75,
                    "acceptance_to_regression_ratio": 2.0,
                    "memory_precision_proxy": 0.80,
                    "holdout_pass_rate": 0.65,
                },
            ),
            patch("hermes_cli.autoresearch._get_hermes_home", return_value=tmp_path),
        ):
            autoresearch_command(make_args(autoresearch_cmd="status"))
        out = capsys.readouterr().out
        assert "operator confidence" in out
        assert "patch_stability_ratio" in out
        assert "holdout_pass_rate" in out


# ── `schedule` subcommand ─────────────────────────────────────────────────────

class TestScheduleCmd:
    def test_schedule_saves_to_config(self, capsys):
        saved = {}

        def fake_save(cfg):
            saved.update(cfg)

        with (
            patch("hermes_cli.autoresearch._load_config", return_value={"autoresearch": {}}),
            patch("hermes_cli.autoresearch._save_config", side_effect=fake_save),
        ):
            rc = autoresearch_command(make_args(autoresearch_cmd="schedule", expr="0 3 * * *"))
        assert rc == 0
        assert saved.get("autoresearch", {}).get("schedule") == "0 3 * * *"

    def test_schedule_without_expr_returns_1(self, capsys):
        rc = autoresearch_command(make_args(autoresearch_cmd="schedule", expr=None))
        assert rc == 1

    def test_schedule_prints_confirmation(self, capsys):
        with (
            patch("hermes_cli.autoresearch._load_config", return_value={"autoresearch": {}}),
            patch("hermes_cli.autoresearch._save_config"),
        ):
            autoresearch_command(make_args(autoresearch_cmd="schedule", expr="0 4 * * 1"))
        out = capsys.readouterr().out
        assert "0 4 * * 1" in out


# ── `patches` subcommand ──────────────────────────────────────────────────────

class TestPatchesCmd:
    def test_patches_no_file(self, capsys, tmp_path):
        with patch("hermes_cli.autoresearch._get_hermes_home", return_value=tmp_path):
            rc = autoresearch_command(make_args(autoresearch_cmd="patches"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "No pending_patches.json" in out

    def test_patches_empty_file(self, capsys, tmp_path):
        (tmp_path / "autoresearch").mkdir()
        (tmp_path / "autoresearch" / "pending_patches.json").write_text("[]")
        with patch("hermes_cli.autoresearch._get_hermes_home", return_value=tmp_path):
            autoresearch_command(make_args(autoresearch_cmd="patches"))
        out = capsys.readouterr().out
        assert "empty" in out.lower()

    def test_patches_shows_skill_names(self, capsys, tmp_path):
        patch_data = [
            {
                "skill_name": "git-workflow",
                "status": "accepted",
                "accepted": True,
                "reason": "Clarify branch naming",
                "token_delta": -0.1,
                "quality_delta": 0.5,
                "rejection_reason": "",
            }
        ]
        (tmp_path / "autoresearch").mkdir()
        (tmp_path / "autoresearch" / "pending_patches.json").write_text(
            json.dumps(patch_data)
        )
        with patch("hermes_cli.autoresearch._get_hermes_home", return_value=tmp_path):
            autoresearch_command(make_args(autoresearch_cmd="patches"))
        out = capsys.readouterr().out
        assert "git-workflow" in out

    def test_patches_shows_recent_memory_outcomes(self, capsys, tmp_path):
        (tmp_path / "autoresearch").mkdir()
        (tmp_path / "autoresearch" / "pending_patches.json").write_text("[]")
        (tmp_path / "autoresearch" / "pending_memory_updates.json").write_text("[]")
        with (
            patch("hermes_cli.autoresearch._get_hermes_home", return_value=tmp_path),
            patch(
                "hermes_cli.autoresearch._recent_memory_outcomes",
                return_value=[
                    {
                        "target": "memory",
                        "action": "replace",
                        "status": "needs_review",
                        "error": "Multiple entries matched",
                    }
                ],
            ),
        ):
            autoresearch_command(make_args(autoresearch_cmd="patches"))
        out = capsys.readouterr().out
        assert "Recent memory outcomes" in out
        assert "needs_review" in out


# ── `enable` / `disable` subcommands ─────────────────────────────────────────

class TestEnableDisableCmd:
    def test_enable_sets_enabled_true(self, capsys):
        saved = {}

        def fake_save(cfg):
            saved.update(cfg)

        with (
            patch("hermes_cli.autoresearch._load_config", return_value={"autoresearch": {"enabled": False}}),
            patch("hermes_cli.autoresearch._save_config", side_effect=fake_save),
        ):
            rc = autoresearch_command(make_args(autoresearch_cmd="enable"))
        assert rc == 0
        assert saved["autoresearch"]["enabled"] is True

    def test_disable_sets_enabled_false(self, capsys):
        saved = {}

        def fake_save(cfg):
            saved.update(cfg)

        with (
            patch("hermes_cli.autoresearch._load_config", return_value={"autoresearch": {"enabled": True}}),
            patch("hermes_cli.autoresearch._save_config", side_effect=fake_save),
        ):
            rc = autoresearch_command(make_args(autoresearch_cmd="disable"))
        assert rc == 0
        assert saved["autoresearch"]["enabled"] is False


# ── Unknown subcommand ────────────────────────────────────────────────────────

class TestUnknownSubcmd:
    def test_unknown_subcmd_returns_1(self, capsys):
        rc = autoresearch_command(make_args(autoresearch_cmd="frobnicate"))
        assert rc == 1
