"""
applier.py — Apply accepted patches from pending_patches.json to skill files.

This is the first Stage 3 component that writes to HERMES_HOME/skills/.

Safety mechanisms:
  Recency lock   — skip any skill that had an [in-session] patch within 24h.
                   Prevents racing with user corrections.
  dry_run flag   — when True, logs what would happen but writes nothing.
  Atomic write   — SKILL.md updated via tempfile + os.replace so partial
                   writes are impossible.
  History entry  — every applied patch is recorded in SKILL_HISTORY.md
                   with source="autoresearch" for full auditability.
  DB record      — every applied patch is recorded in autoresearch_patches
                   table so regression_watch can check metrics the next night.

Public API:
  apply_patches(pairs, metrics_conn, hermes_home, dry_run=False)
    → list[ApplyResult]

ApplyResult keys:
  skill_name    str
  status        str   "applied" | "deferred" | "failed" | "dry_run"
  reason        str   why deferred/failed, or the patch reason when applied
  old_string    str
  new_string    str
"""

import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cron.autoresearch.skill_metrics import record_autoresearch_patch

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

RECENCY_LOCK_HOURS = 24
HISTORY_HEADER_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) — (\w+) \[(.+)\]$"
)


# ── Types ─────────────────────────────────────────────────────────────────────

ApplyResult = Dict[str, Any]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _last_in_session_patch_ts(skill_dir: Path) -> Optional[datetime]:
    """Scan SKILL_HISTORY.md and return the timestamp of the most recent
    [in-session] entry, or None if no such entry exists."""
    history_file = skill_dir / "SKILL_HISTORY.md"
    if not history_file.exists():
        return None

    latest: Optional[datetime] = None
    for line in history_file.read_text(encoding="utf-8").splitlines():
        m = HISTORY_HEADER_RE.match(line)
        if m and m.group(3) == "in-session":
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            if latest is None or ts > latest:
                latest = ts
    return latest


def _is_recency_locked(skill_dir: Path, lock_hours: int = RECENCY_LOCK_HOURS) -> bool:
    """Return True if the skill was patched in-session within lock_hours."""
    last_ts = _last_in_session_patch_ts(skill_dir)
    if last_ts is None:
        return False
    age = datetime.now(timezone.utc) - last_ts
    return age < timedelta(hours=lock_hours)


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_history(
    skill_dir: Path,
    action: str,
    reason: str,
    old_string: str,
    new_string: str,
    source: str,
) -> None:
    """Append one record to SKILL_HISTORY.md."""
    history_path = skill_dir / "SKILL_HISTORY.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = (
        f"\n## {now} — {action} [{source}]\n"
        f"**Reason:** {reason}\n"
        f"**File:** SKILL.md\n"
        f"\n### Old\n```text\n{old_string}\n```\n"
        f"\n### New\n```text\n{new_string}\n```\n"
    )
    existing = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    _atomic_write(history_path, existing + record)


# ── Public API ────────────────────────────────────────────────────────────────

def apply_patches(
    patches: List[Dict[str, Any]],
    metrics_conn,
    hermes_home: Path,
    dry_run: bool = False,
) -> List[ApplyResult]:
    """Apply accepted patches to skill files.

    Args:
        patches:      List of pending_patches.json entries (from read_pending_patches).
        metrics_conn: Open connection to skill_metrics.db.
        hermes_home:  Root of HERMES_HOME (tests pass tmp_path here).
        dry_run:      If True, log what would happen but write nothing.

    Returns:
        List of ApplyResult dicts, one per accepted patch that was processed.
    """
    results: List[ApplyResult] = []

    for patch in patches:
        if not patch.get("accepted"):
            continue

        skill_name = patch["skill_name"]
        old_string = patch["old_string"]
        new_string = patch["new_string"]
        reason = patch.get("reason", "autoresearch patch")

        skill_dir = hermes_home / "skills" / skill_name
        skill_md = skill_dir / "SKILL.md"

        if not skill_md.exists():
            logger.warning("apply: SKILL.md not found for '%s' — skipping", skill_name)
            results.append({
                "skill_name": skill_name,
                "status": "failed",
                "reason": "SKILL.md not found",
                "old_string": old_string,
                "new_string": new_string,
            })
            continue

        # Recency lock check
        if _is_recency_locked(skill_dir):
            logger.info(
                "apply: '%s' had an in-session patch within %dh — deferring",
                skill_name, RECENCY_LOCK_HOURS,
            )
            results.append({
                "skill_name": skill_name,
                "status": "deferred",
                "reason": f"in-session patch within {RECENCY_LOCK_HOURS}h",
                "old_string": old_string,
                "new_string": new_string,
            })
            continue

        # Verify old_string still present
        content = skill_md.read_text(encoding="utf-8")
        if old_string not in content:
            logger.warning(
                "apply: old_string no longer in '%s' SKILL.md — skipping (stale patch)",
                skill_name,
            )
            results.append({
                "skill_name": skill_name,
                "status": "failed",
                "reason": "old_string not found in current SKILL.md (patch is stale)",
                "old_string": old_string,
                "new_string": new_string,
            })
            continue

        if dry_run:
            logger.info("dry_run: would apply patch to '%s'", skill_name)
            results.append({
                "skill_name": skill_name,
                "status": "dry_run",
                "reason": reason,
                "old_string": old_string,
                "new_string": new_string,
            })
            continue

        # Apply patch
        new_content = content.replace(old_string, new_string, 1)
        _atomic_write(skill_md, new_content)
        logger.info("apply: patched SKILL.md for '%s'", skill_name)

        # Record in SKILL_HISTORY.md
        _append_history(
            skill_dir=skill_dir,
            action="patch",
            reason=reason,
            old_string=old_string,
            new_string=new_string,
            source="autoresearch",
        )

        # Record in autoresearch_patches DB table (for regression watch)
        record_autoresearch_patch(
            conn=metrics_conn,
            skill_name=skill_name,
            patch_type="patch",
            baseline_correction_rate=patch.get("correction_rate", 0.0),
            baseline_completion_rate=patch.get("completion_rate", 0.0),
            baseline_tokens=patch.get("avg_tokens", 0.0),
            old_string=old_string,
            new_string=new_string,
        )

        results.append({
            "skill_name": skill_name,
            "status": "applied",
            "reason": reason,
            "old_string": old_string,
            "new_string": new_string,
        })

    return results
