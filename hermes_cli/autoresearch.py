"""
hermes_cli/autoresearch.py — CLI handler for `hermes autoresearch`.

Subcommands
───────────
run [--dry-run]   Run the full autoresearch loop immediately.
status            Show last run status and config summary.
schedule <expr>   Set the cron schedule (e.g. "0 2 * * *").
patches           Print pending_patches.json in readable form.
disable           Set autoresearch.enabled = false in config.
enable            Set autoresearch.enabled = true in config.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_hermes_home() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home()


def _load_config() -> dict:
    from hermes_cli.config import load_config
    return load_config()


def _save_config(config: dict) -> None:
    from hermes_cli.config import save_config
    save_config(config)


def _get_autoresearch_config(config: dict) -> dict:
    return config.get("autoresearch", {})


def _load_run_state() -> dict:
    from cron.autoresearch.runner import load_run_state
    return load_run_state()


def _read_pending_patches(patches_path: Optional[Path] = None) -> list:
    from cron.autoresearch.pending_patches import read_pending_patches
    return read_pending_patches(path=patches_path)


# ── Subcommand handlers ───────────────────────────────────────────────────────

def _cmd_run(args) -> int:
    """Run the full autoresearch loop immediately."""
    dry_run = getattr(args, "dry_run", False)
    print(f"Running autoresearch loop{' (dry-run)' if dry_run else ''}...")
    try:
        from cron.autoresearch.runner import run_full_loop
        digest = run_full_loop(dry_run=dry_run)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print()
    print(digest)
    return 0


def _cmd_status(args) -> int:
    """Show last run status and config summary."""
    config = _load_config()
    ar = _get_autoresearch_config(config)
    state = _load_run_state()

    enabled = ar.get("enabled", True)
    schedule = ar.get("schedule", "0 2 * * *")
    dry_run = ar.get("dry_run", False)
    deliver = ar.get("deliver", [])

    last_run = state.get("last_run_at") or "never"
    last_status = state.get("last_status") or "—"
    last_error = state.get("last_error")

    status_icon = {"ok": "✓", "error": "✗"}.get(last_status, "—")

    lines = [
        "Autoresearch status",
        "───────────────────",
        f"  enabled:     {enabled}",
        f"  schedule:    {schedule}",
        f"  dry_run:     {dry_run}",
        f"  deliver:     {deliver or '(none)'}",
        "",
        f"  last run:    {last_run}",
        f"  last status: {status_icon} {last_status}",
    ]
    if last_error:
        lines.append(f"  last error:  {last_error}")

    print("\n".join(lines))
    return 0


def _cmd_schedule(args) -> int:
    """Set the cron schedule for the autoresearch loop."""
    expr = getattr(args, "expr", None)
    if not expr:
        print("Error: missing schedule expression (e.g. '0 2 * * *')", file=sys.stderr)
        return 1

    # Validate via croniter if available
    try:
        import croniter
        if not croniter.croniter.is_valid(expr):
            print(f"Error: invalid cron expression: {expr!r}", file=sys.stderr)
            return 1
    except ImportError:
        pass  # croniter not installed; accept the expression

    config = _load_config()
    config.setdefault("autoresearch", {})["schedule"] = expr
    _save_config(config)
    print(f"Autoresearch schedule set to: {expr}")
    return 0


def _cmd_patches(args) -> int:
    """Print pending_patches.json in a readable form."""
    home = _get_hermes_home()
    patches_path = home / "autoresearch" / "pending_patches.json"

    if not patches_path.exists():
        print("No pending_patches.json found.")
        return 0

    patches = _read_pending_patches(patches_path)
    if not patches:
        print("pending_patches.json is empty.")
        return 0

    print(f"Pending patches ({len(patches)}):\n")
    for i, p in enumerate(patches, 1):
        skill = p.get("skill_name", "?")
        status = p.get("status", "?")
        accepted = p.get("accepted", False)
        reason = p.get("reason", "")
        token_delta = p.get("token_delta")
        quality_delta = p.get("quality_delta")
        rejection_reason = p.get("rejection_reason", "")

        icon = "✓" if accepted else "✗"
        delta_str = ""
        if token_delta is not None and quality_delta is not None:
            delta_str = f"  token Δ={token_delta:+.0%}  quality Δ={quality_delta:+.2f}"

        print(f"  {i}. [{icon}] {skill} — {status}")
        if reason:
            print(f"       reason: {reason}")
        if delta_str:
            print(f"      {delta_str}")
        if rejection_reason:
            print(f"       rejected: {rejection_reason}")
    return 0


def _cmd_enable(args) -> int:
    """Enable the autoresearch loop."""
    config = _load_config()
    config.setdefault("autoresearch", {})["enabled"] = True
    _save_config(config)
    print("Autoresearch enabled.")
    return 0


def _cmd_disable(args) -> int:
    """Disable the autoresearch loop."""
    config = _load_config()
    config.setdefault("autoresearch", {})["enabled"] = False
    _save_config(config)
    print("Autoresearch disabled.")
    return 0


# ── Main dispatcher ───────────────────────────────────────────────────────────

def autoresearch_command(args) -> int:
    """Dispatch hermes autoresearch subcommands."""
    subcmd = getattr(args, "autoresearch_cmd", None)
    handlers = {
        "run": _cmd_run,
        "status": _cmd_status,
        "schedule": _cmd_schedule,
        "patches": _cmd_patches,
        "enable": _cmd_enable,
        "disable": _cmd_disable,
    }
    handler = handlers.get(subcmd)
    if handler is None:
        print(
            "Usage: hermes autoresearch {run,status,schedule,patches,enable,disable}",
            file=sys.stderr,
        )
        return 1
    return handler(args)
