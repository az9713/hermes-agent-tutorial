"""
pending_patches.py — Writes ~/.hermes/autoresearch/pending_patches.json.

Each entry in the JSON file represents one evaluated candidate patch with its
self-play result and final status. Stage 3 reads this file to decide what to
apply. Operators can inspect it before Stage 3 is enabled.

Schema per entry:
  skill_name        str
  anomaly_type      str    — "UNDERPERFORMING"
  trigger_metric    str    — e.g. "correction_rate=0.41"
  action            str    — always "patch" for Stage 2
  status            str    — "accepted" | "rejected" | "hold"
  accepted          bool
  token_delta       float
  quality_delta     float
  judge_scores      list   — [[old, new], ...]
  old_string        str
  new_string        str
  reason            str
  hold_reason       str
  rejection_reason  str
  generated_at      str    — UTC ISO-8601

Public API:
  write_pending_patches(patches, path) -> str   (returns JSON text)
  read_pending_patches(path) -> list[dict]
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home


def get_default_patches_path() -> Path:
    return get_hermes_home() / "autoresearch" / "pending_patches.json"


def _build_entry(
    candidate: Dict[str, Any],
    eval_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge a CandidatePatch and an EvalResult into one pending_patches entry."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "skill_name": candidate["skill_name"],
        "anomaly_type": candidate["anomaly_type"],
        "trigger_metric": candidate["trigger_metric"],
        "action": "patch",
        "status": eval_result["status"],
        "accepted": eval_result["accepted"],
        "token_delta": eval_result["token_delta"],
        "quality_delta": eval_result["quality_delta"],
        "judge_scores": eval_result["judge_scores"],
        "old_string": candidate["old_string"],
        "new_string": candidate["new_string"],
        "reason": candidate.get("reason", ""),
        "hold_reason": eval_result.get("hold_reason", ""),
        "rejection_reason": eval_result.get("rejection_reason", ""),
        "generated_at": now,
    }
    # Optional extended evaluation fields (holdout, rubric, dual-judge).
    for key in (
        "holdout_task_count",
        "holdout_token_delta",
        "holdout_quality_delta",
        "holdout_pass",
        "rubric_pass_rate_old",
        "rubric_pass_rate_new",
        "holdout_rubric_pass_rate_old",
        "holdout_rubric_pass_rate_new",
        "dual_judge_disagreement",
        "primary_quality_delta",
        "secondary_quality_delta",
    ):
        if key in eval_result:
            entry[key] = eval_result[key]
    return entry


def write_pending_patches(
    pairs: List[Dict[str, Any]],
    path: Optional[Path] = None,
) -> str:
    """Write pending_patches.json from a list of (candidate, eval_result) dicts.

    Args:
        pairs:  List of dicts, each with keys "candidate" and "eval_result".
        path:   File path. Defaults to HERMES_HOME/autoresearch/pending_patches.json.

    Returns:
        The JSON string that was written.
    """
    output_path = path or get_default_patches_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entries = [
        _build_entry(p["candidate"], p["eval_result"])
        for p in pairs
    ]

    text = json.dumps(entries, indent=2)
    output_path.write_text(text, encoding="utf-8")
    return text


def read_pending_patches(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read pending_patches.json. Returns empty list if file does not exist."""
    file_path = path or get_default_patches_path()
    if not file_path.exists():
        return []
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
