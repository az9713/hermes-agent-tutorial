"""
Tests for _tick_autoresearch() in cron/scheduler.py.

What we're testing
──────────────────
_tick_autoresearch() reads config, checks the croniter schedule, and fires
run_full_loop() when the loop is due. These tests verify:

1. autoresearch.enabled=False → returns False, loop not called.
2. Loop is not yet due → returns False, loop not called.
3. Loop never ran → treated as overdue, loop called.
4. Loop is due (next occurrence already passed) → returns True, loop called.
5. deliver_digest() called with configured platforms after run.
6. deliver_digest() errors are logged, not raised.
7. run_full_loop() exception → returns True (ran), does not propagate.
8. croniter not installed → returns False gracefully.
9. Config load failure → returns False gracefully.
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from cron.scheduler import _tick_autoresearch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(enabled=True, schedule="0 2 * * *", deliver=None, dry_run=False):
    return {
        "autoresearch": {
            "enabled": enabled,
            "schedule": schedule,
            "dry_run": dry_run,
            "deliver": deliver or [],
        }
    }


def _state_last_ran(hours_ago: float) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"last_run_at": ts, "last_status": "ok", "last_error": None}


def _state_never() -> dict:
    return {"last_run_at": None, "last_status": None, "last_error": None}


def _make_croniter_mock(is_due: bool):
    """Return a mock croniter module that says the schedule is (or isn't) due."""
    now = datetime.now(timezone.utc)
    if is_due:
        # next occurrence is 1 minute in the past
        next_dt = now - timedelta(minutes=1)
    else:
        # next occurrence is 59 minutes in the future
        next_dt = now + timedelta(minutes=59)

    mock_croniter_instance = MagicMock()
    mock_croniter_instance.get_next.return_value = next_dt

    mock_croniter_cls = MagicMock(return_value=mock_croniter_instance)
    mock_croniter_cls.is_valid = MagicMock(return_value=True)

    mock_croniter_module = MagicMock()
    mock_croniter_module.croniter = mock_croniter_cls
    return mock_croniter_module


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTickAutoresearch:
    def test_disabled_returns_false(self):
        """enabled=False → returns False without touching croniter."""
        with (
            patch("hermes_cli.config.load_config", return_value=_make_config(enabled=False)),
        ):
            result = _tick_autoresearch()
        assert result is False

    def test_never_ran_calls_loop(self):
        """Never-ran state → loop treated as overdue → called."""
        mock_croniter = _make_croniter_mock(is_due=True)
        with (
            patch("hermes_cli.config.load_config", return_value=_make_config(schedule="* * * * *")),
            patch("cron.autoresearch.runner.load_run_state", return_value=_state_never()),
            patch("cron.autoresearch.runner.run_full_loop", return_value="digest") as mock_loop,
            patch("cron.autoresearch.runner.deliver_digest", return_value={}),
            patch.dict(sys.modules, {"croniter": mock_croniter}),
        ):
            result = _tick_autoresearch()
        mock_loop.assert_called_once()
        assert result is True

    def test_loop_not_yet_due_returns_false(self):
        """Next occurrence is in the future → not due, loop not called."""
        mock_croniter = _make_croniter_mock(is_due=False)
        with (
            patch("hermes_cli.config.load_config", return_value=_make_config(schedule="0 * * * *")),
            patch("cron.autoresearch.runner.load_run_state", return_value=_state_last_ran(0.5)),
            patch("cron.autoresearch.runner.run_full_loop") as mock_loop,
            patch.dict(sys.modules, {"croniter": mock_croniter}),
        ):
            result = _tick_autoresearch()
        mock_loop.assert_not_called()
        assert result is False

    def test_loop_due_calls_run_full_loop(self):
        """Next occurrence is in the past → due, loop called."""
        mock_croniter = _make_croniter_mock(is_due=True)
        with (
            patch("hermes_cli.config.load_config", return_value=_make_config(schedule="* * * * *")),
            patch("cron.autoresearch.runner.load_run_state", return_value=_state_last_ran(2)),
            patch("cron.autoresearch.runner.run_full_loop", return_value="digest") as mock_loop,
            patch("cron.autoresearch.runner.deliver_digest", return_value={}),
            patch.dict(sys.modules, {"croniter": mock_croniter}),
        ):
            result = _tick_autoresearch()
        mock_loop.assert_called_once()
        assert result is True

    def test_deliver_called_with_platforms(self):
        """When deliver=['slack','telegram'], deliver_digest gets both."""
        mock_croniter = _make_croniter_mock(is_due=True)
        config = _make_config(schedule="* * * * *", deliver=["slack", "telegram"])
        with (
            patch("hermes_cli.config.load_config", return_value=config),
            patch("cron.autoresearch.runner.load_run_state", return_value=_state_never()),
            patch("cron.autoresearch.runner.run_full_loop", return_value="digest"),
            patch("cron.autoresearch.runner.deliver_digest", return_value={}) as mock_deliver,
            patch.dict(sys.modules, {"croniter": mock_croniter}),
        ):
            _tick_autoresearch()
        mock_deliver.assert_called_once_with("digest", ["slack", "telegram"])

    def test_deliver_error_does_not_propagate(self):
        """Delivery failure is logged, not raised."""
        mock_croniter = _make_croniter_mock(is_due=True)
        config = _make_config(schedule="* * * * *", deliver=["slack"])
        with (
            patch("hermes_cli.config.load_config", return_value=config),
            patch("cron.autoresearch.runner.load_run_state", return_value=_state_never()),
            patch("cron.autoresearch.runner.run_full_loop", return_value="digest"),
            patch("cron.autoresearch.runner.deliver_digest", return_value={"slack": "error: no channel"}),
            patch.dict(sys.modules, {"croniter": mock_croniter}),
        ):
            result = _tick_autoresearch()
        assert result is True

    def test_run_full_loop_exception_returns_true(self):
        """Loop exception is caught; function still returns True (ran)."""
        mock_croniter = _make_croniter_mock(is_due=True)
        with (
            patch("hermes_cli.config.load_config", return_value=_make_config(schedule="* * * * *")),
            patch("cron.autoresearch.runner.load_run_state", return_value=_state_never()),
            patch("cron.autoresearch.runner.run_full_loop", side_effect=RuntimeError("stage 3 borked")),
            patch.dict(sys.modules, {"croniter": mock_croniter}),
        ):
            result = _tick_autoresearch()
        assert result is True

    def test_croniter_not_installed_returns_false(self):
        """If croniter import fails, tick returns False gracefully."""
        # Remove croniter from sys.modules to force ImportError
        with patch.dict(sys.modules, {"croniter": None}):
            with (
                patch("hermes_cli.config.load_config", return_value=_make_config()),
                patch("cron.autoresearch.runner.load_run_state", return_value=_state_last_ran(25)),
            ):
                result = _tick_autoresearch()
        assert result is False

    def test_config_load_failure_returns_false(self):
        """Config load exception → graceful return False."""
        with patch("hermes_cli.config.load_config", side_effect=RuntimeError("config gone")):
            result = _tick_autoresearch()
        assert result is False

    def test_no_deliver_platforms_skips_deliver(self):
        """Empty deliver list → deliver_digest not called."""
        mock_croniter = _make_croniter_mock(is_due=True)
        config = _make_config(schedule="* * * * *", deliver=[])
        with (
            patch("hermes_cli.config.load_config", return_value=config),
            patch("cron.autoresearch.runner.load_run_state", return_value=_state_never()),
            patch("cron.autoresearch.runner.run_full_loop", return_value="digest"),
            patch("cron.autoresearch.runner.deliver_digest") as mock_deliver,
            patch.dict(sys.modules, {"croniter": mock_croniter}),
        ):
            _tick_autoresearch()
        mock_deliver.assert_not_called()
