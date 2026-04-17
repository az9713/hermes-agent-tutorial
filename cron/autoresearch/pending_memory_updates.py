"""
pending_memory_updates.py -- JSON artifact for memory update proposals.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home


def get_default_memory_updates_path() -> Path:
    return get_hermes_home() / "autoresearch" / "pending_memory_updates.json"


def write_pending_memory_updates(
    proposals: List[Dict[str, Any]],
    path: Optional[Path] = None,
) -> str:
    output = path or get_default_memory_updates_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries: List[Dict[str, Any]] = []
    for p in proposals:
        entries.append(
            {
                "target": p["target"],
                "action": p["action"],
                "old_text": p["old_text"],
                "content": p.get("content", ""),
                "reason": p.get("reason", ""),
                "confidence": float(p.get("confidence", 0.0)),
                "evidence_count": int(p.get("evidence_count", 0)),
                "trigger_metric": p.get("trigger_metric", ""),
                "generated_at": now,
            }
        )
    text = json.dumps(entries, indent=2)
    output.write_text(text, encoding="utf-8")
    return text


def read_pending_memory_updates(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    file_path = path or get_default_memory_updates_path()
    if not file_path.exists():
        return []
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return []
