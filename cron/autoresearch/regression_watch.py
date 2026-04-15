"""
regression_watch.py — Check post-patch metrics and roll back if worse.

Runs the night after apply_patches(). For each patch recorded in
autoresearch_patches with status='applied', compares the current
7-day correction_rate to the baseline stored at patch time.

Decision tree per patch:
  1. In-session patches since autoresearch patch?
     YES → causation ambiguous → flag for human review ("needs_review").
     NO  → safe to compare metrics.
  2. correction_rate delta > ROLLBACK_THRESHOLD?
     YES → rollback: restore old_string, tag history [autoresearch: regression-watch].
     NO  → stable: update status to "stable".

Public API:
  check_regressions(metrics_conn, hermes_home, patches_since_ts=None)
    → list[WatchResult]

WatchResult keys:
  skill_name      str
  patch_id        int
  status          str   "stable" | "rolled_back" | "needs_review"
  correction_rate_delta  float
  reason          str
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cron.autoresearch.skill_metrics import (
    get_applied_patches,
    get_skill_health_summary,
    update_patch_status,
)
from cron.autoresearch.applier import (
    HISTORY_HEADER_RE,
    _append_history,
    _atomic_write,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

ROLLBACK_THRESHOLD = 0.15   # rollback if correction_rate rose by more than this


# ── Types ─────────────────────────────────────────────────────────────────────

WatchResult = Dict[str, Any]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _count_in_session_patches_since(skill_dir: Path, since_ts: str) -> int:
    """Count [in-session] SKILL_HISTORY.md entries written after since_ts.

    since_ts is a UTC ISO-8601 string (from autoresearch_patches.patch_applied_at).
    """
    history_file = skill_dir / "SKILL_HISTORY.md"
    if not history_file.exists():
        return 0

    try:
        cutoff = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
    except ValueError:
        return 0

    count = 0
    for line in history_file.read_text(encoding="utf-8").splitlines():
        m = HISTORY_HEADER_RE.match(line)
        if m and m.group(3) == "in-session":
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            if ts > cutoff:
                count += 1
    return count


def _get_current_correction_rate(metrics_conn, skill_name: str, days: int = 7) -> Optional[float]:
    """Return the current weighted-average correction_rate for a skill.

    Returns None if the skill has no health rows in the window.
    """
    summary = get_skill_health_summary(metrics_conn, days=days)
    for row in summary:
        if row["skill_name"] == skill_name:
            return row.get("correction_rate")
    return None


def _rollback_skill(
    skill_dir: Path,
    old_string: str,
    new_string: str,
    rollback_reason: str,
) -> bool:
    """Restore old_string in SKILL.md (undo the autoresearch patch).

    Returns True if rollback succeeded, False if new_string not found
    (already rolled back or manually edited).
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False

    content = skill_md.read_text(encoding="utf-8")
    if new_string not in content:
        return False

    restored = content.replace(new_string, old_string, 1)
    _atomic_write(skill_md, restored)

    _append_history(
        skill_dir=skill_dir,
        action="patch",
        reason=rollback_reason,
        old_string=new_string,
        new_string=old_string,
        source="autoresearch: regression-watch",
    )
    return True


# ── Public API ────────────────────────────────────────────────────────────────

def check_regressions(
    metrics_conn,
    hermes_home: Path,
    patches_since_ts: Optional[str] = None,
    rollback_threshold: float = ROLLBACK_THRESHOLD,
) -> List[WatchResult]:
    """Check post-patch metrics for all applied autoresearch patches.

    Args:
        metrics_conn:     Open connection to skill_metrics.db.
        hermes_home:      Root HERMES_HOME (tests pass tmp_path).
        patches_since_ts: Only examine patches applied at or after this UTC
                          ISO-8601 timestamp. If None, checks all applied patches.
        rollback_threshold: Rollback if correction_rate delta exceeds this.

    Returns:
        List of WatchResult dicts, one per examined patch.
    """
    applied = get_applied_patches(metrics_conn, since_ts=patches_since_ts)
    logger.info("regression_watch: examining %d applied patch(es)", len(applied))

    results: List[WatchResult] = []

    for patch_row in applied:
        patch_id = patch_row["id"]
        skill_name = patch_row["skill_name"]
        patch_applied_at = patch_row["patch_applied_at"]
        baseline_correction_rate = patch_row["baseline_correction_rate"] or 0.0

        skill_dir = hermes_home / "skills" / skill_name

        # 1. Check for in-session patches since autoresearch patch
        in_session_count = _count_in_session_patches_since(skill_dir, patch_applied_at)
        if in_session_count > 0:
            logger.info(
                "regression_watch: '%s' had %d in-session patch(es) since autoresearch — "
                "causation ambiguous, flagging for review",
                skill_name, in_session_count,
            )
            update_patch_status(metrics_conn, patch_id, "needs_review")
            results.append({
                "skill_name": skill_name,
                "patch_id": patch_id,
                "status": "needs_review",
                "correction_rate_delta": 0.0,
                "reason": (
                    f"{in_session_count} in-session patch(es) since autoresearch patch — "
                    "cannot determine cause of metric change"
                ),
            })
            continue

        # 2. Get current correction_rate
        current_rate = _get_current_correction_rate(metrics_conn, skill_name)
        if current_rate is None:
            logger.info(
                "regression_watch: no health data for '%s' — skipping", skill_name
            )
            continue

        delta = current_rate - baseline_correction_rate
        logger.info(
            "regression_watch: '%s' correction_rate delta=%.2f (baseline=%.2f, current=%.2f)",
            skill_name, delta, baseline_correction_rate, current_rate,
        )

        if delta > rollback_threshold:
            # Roll back
            rollback_reason = (
                f"autoresearch regression-watch: correction_rate rose "
                f"+{delta:.0%} (baseline {baseline_correction_rate:.0%} → "
                f"current {current_rate:.0%})"
            )
            rolled_back = _rollback_skill(
                skill_dir=skill_dir,
                old_string=patch_row.get("old_string", ""),
                new_string=patch_row.get("new_string", ""),
                rollback_reason=rollback_reason,
            )
            status = "rolled_back" if rolled_back else "needs_review"
            update_patch_status(metrics_conn, patch_id, status)
            results.append({
                "skill_name": skill_name,
                "patch_id": patch_id,
                "status": status,
                "correction_rate_delta": delta,
                "reason": rollback_reason,
            })
        else:
            update_patch_status(metrics_conn, patch_id, "stable")
            results.append({
                "skill_name": skill_name,
                "patch_id": patch_id,
                "status": "stable",
                "correction_rate_delta": delta,
                "reason": f"correction_rate stable (delta={delta:+.0%})",
            })

    return results
