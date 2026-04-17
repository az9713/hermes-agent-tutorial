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


def _read_pending_memory_updates(path: Optional[Path] = None) -> list:
    from cron.autoresearch.pending_memory_updates import read_pending_memory_updates
    return read_pending_memory_updates(path=path)


def _memory_update_counts(home: Path) -> dict:
    from cron.autoresearch.skill_metrics import open_db, get_memory_update_counts
    db_path = home / "autoresearch" / "skill_metrics.db"
    conn = open_db(db_path)
    try:
        return get_memory_update_counts(conn)
    finally:
        conn.close()


def _operator_confidence_metrics(home: Path, days: int = 30) -> dict:
    from cron.autoresearch.skill_metrics import open_db, get_operator_confidence_metrics
    db_path = home / "autoresearch" / "skill_metrics.db"
    conn = open_db(db_path)
    try:
        return get_operator_confidence_metrics(conn, days=days)
    finally:
        conn.close()


def _recent_memory_outcomes(home: Path, limit: int = 5) -> list:
    from cron.autoresearch.skill_metrics import open_db, list_memory_updates
    db_path = home / "autoresearch" / "skill_metrics.db"
    conn = open_db(db_path)
    try:
        rows = list_memory_updates(conn)
    finally:
        conn.close()
    closed = [
        r for r in rows
        if r.get("status") in {"applied", "discarded", "needs_review", "failed"}
    ]
    return closed[:limit]


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
    home = _get_hermes_home()
    try:
        counts = _memory_update_counts(home)
        lines.extend(
            [
                "",
                "  memory queue:",
                f"    proposed:            {counts.get('proposed', 0)}",
                f"    pending_revalidation:{counts.get('pending_revalidation', 0)}",
                f"    needs_review:        {counts.get('needs_review', 0)}",
                "  memory outcomes:",
                f"    applied:             {counts.get('applied', 0)}",
                f"    discarded:           {counts.get('discarded', 0)}",
                f"    failed:              {counts.get('failed', 0)}",
            ]
        )
    except Exception:
        pass
    try:
        metrics = _operator_confidence_metrics(home, days=30)
        lines.extend(
            [
                "",
                "  operator confidence (30d):",
                f"    patch_stability_ratio:      {float(metrics.get('patch_stability_ratio', 0.0)):.1%}",
                f"    acceptance/regression:      {float(metrics.get('acceptance_to_regression_ratio', 0.0)):.2f}",
                f"    memory_precision_proxy:     {float(metrics.get('memory_precision_proxy', 0.0)):.1%}",
                f"    holdout_pass_rate:          {float(metrics.get('holdout_pass_rate', 0.0)):.1%}",
            ]
        )
    except Exception:
        pass
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
    """Print pending skill and memory updates in readable form."""
    home = _get_hermes_home()
    patches_path = home / "autoresearch" / "pending_patches.json"
    memory_updates_path = home / "autoresearch" / "pending_memory_updates.json"

    if not patches_path.exists() and not memory_updates_path.exists():
        print("No pending_patches.json or pending_memory_updates.json found.")
        return 0

    patches = _read_pending_patches(patches_path)
    mem_updates = _read_pending_memory_updates(memory_updates_path)

    if patches:
        print(f"Pending skill patches ({len(patches)}):\n")
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
    else:
        print("pending_patches.json is empty.")

    print()
    if mem_updates:
        print(f"Pending memory updates ({len(mem_updates)}):\n")
        for i, m in enumerate(mem_updates, 1):
            tgt = m.get("target", "?")
            action = m.get("action", "?")
            conf = float(m.get("confidence", 0.0))
            evidence = int(m.get("evidence_count", 0))
            reason = m.get("reason", "")
            print(f"  {i}. {tgt}.{action} confidence={conf:.2f} evidence={evidence}")
            if reason:
                print(f"       reason: {reason}")
    else:
        print("pending_memory_updates.json is empty.")

    outcomes = _recent_memory_outcomes(home, limit=5)
    if outcomes:
        print()
        print("Recent memory outcomes:\n")
        for i, row in enumerate(outcomes, 1):
            target = row.get("target", "?")
            action = row.get("action", "?")
            status = row.get("status", "?")
            err = row.get("error", "")
            print(f"  {i}. memory/{target}.{action} -> {status}")
            if err:
                print(f"       detail: {err}")
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
