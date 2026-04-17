"""
memory_updater.py -- Two-phase memory update apply flow for Stage 3.

Lifecycle:
  proposed -> pending_revalidation -> applied|discarded|needs_review|failed
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

from cron.autoresearch.memory_anomaly_detector import detect_memory_anomalies
from cron.autoresearch.skill_metrics import (
    get_due_memory_updates,
    list_memory_updates,
    update_memory_update_status,
)
from tools.memory_tool import MemoryStore, memory_tool


@contextmanager
def _temporary_hermes_home(hermes_home: Path):
    prev = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(hermes_home)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = prev


def _memory_update_still_supported(
    update_row: Dict[str, Any],
    anomalies: List[Dict[str, Any]],
) -> bool:
    target = update_row.get("target", "")
    old_text = update_row.get("old_text", "")
    for anomaly in anomalies:
        if anomaly.get("target") != target:
            continue
        entry_text = anomaly.get("entry_text", "")
        if old_text and (old_text in entry_text or entry_text[:80] in old_text):
            return True
    return False


def _apply_with_builtin_memory(
    update_row: Dict[str, Any],
    hermes_home: Path,
) -> Dict[str, Any]:
    with _temporary_hermes_home(hermes_home):
        store = MemoryStore()
        store.load_from_disk()
        payload = memory_tool(
            action=update_row["action"],
            target=update_row["target"],
            old_text=update_row["old_text"],
            content=update_row.get("new_content", ""),
            store=store,
        )
    try:
        parsed = json.loads(payload)
    except Exception:
        return {
            "ok": False,
            "status": "failed",
            "error": "memory_tool returned non-JSON response",
        }
    if parsed.get("success"):
        return {"ok": True, "status": "applied", "error": None}
    err = str(parsed.get("error", "unknown memory tool error"))
    lower = err.lower()
    if "multiple entries matched" in lower or "no entry matched" in lower:
        return {"ok": False, "status": "needs_review", "error": err}
    return {"ok": False, "status": "failed", "error": err}


def process_memory_updates(
    metrics_conn,
    hermes_home: Path,
    *,
    anomaly_days: int = 7,
    min_revalidation_evidence: int = 2,
) -> Dict[str, List[Dict[str, Any]]]:
    """Revalidate and apply due memory updates.

    Returns a dict with three lists:
      - proposed: open proposals currently queued (for digest visibility)
      - applied: updates applied this run
      - results: all due updates processed this run
    """
    open_proposed = list_memory_updates(
        metrics_conn, statuses=("proposed", "pending_revalidation")
    )
    due = get_due_memory_updates(metrics_conn)
    if not due:
        return {"proposed": open_proposed, "applied": [], "results": []}

    anomalies = detect_memory_anomalies(
        metrics_conn,
        hermes_home,
        days=anomaly_days,
        min_evidence=min_revalidation_evidence,
        min_evidence_score=0.5,
    )

    applied: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for row in due:
        update_id = int(row["id"])
        update_memory_update_status(
            metrics_conn, update_id, "pending_revalidation", error=None
        )

        if not _memory_update_still_supported(row, anomalies):
            update_memory_update_status(
                metrics_conn,
                update_id,
                "discarded",
                error="staleness signal no longer present at revalidation time",
            )
            results.append(
                {
                    "id": update_id,
                    "target": row["target"],
                    "action": row["action"],
                    "status": "discarded",
                    "reason": "signal no longer present",
                }
            )
            continue

        outcome = _apply_with_builtin_memory(row, hermes_home)
        status = outcome["status"]
        if status == "applied":
            update_memory_update_status(
                metrics_conn,
                update_id,
                "applied",
                error=None,
                set_applied_at=True,
            )
            record = {
                "id": update_id,
                "target": row["target"],
                "action": row["action"],
                "status": "applied",
                "reason": row.get("reason", ""),
            }
            applied.append(record)
            results.append(record)
            continue

        update_memory_update_status(
            metrics_conn,
            update_id,
            status,
            error=outcome.get("error"),
        )
        results.append(
            {
                "id": update_id,
                "target": row["target"],
                "action": row["action"],
                "status": status,
                "reason": outcome.get("error", ""),
            }
        )

    return {
        "proposed": list_memory_updates(
            metrics_conn, statuses=("proposed", "pending_revalidation")
        ),
        "applied": applied,
        "results": results,
    }
