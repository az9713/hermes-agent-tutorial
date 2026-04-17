"""
memory_anomaly_detector.py -- Detect likely stale memory entries.

Built-in file memory only (MEMORY.md / USER.md).
"""

from __future__ import annotations

import json
import re
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

ENTRY_DELIMITER = "\n§\n"
NEGATION_RE = re.compile(
    r"\b(no|not|never|wrong|incorrect|dont|don't|isn't|isnt|shouldn't|shouldnt|misunderstood)\b",
    re.IGNORECASE,
)
STRONG_NEGATION_RE = re.compile(
    r"\b(wrong|incorrect|never|misunderstood)\b", re.IGNORECASE
)
AMBIGUOUS_RE = re.compile(
    r"\b(maybe|might|i\s+think|not\s+sure|possibly|perhaps|could\s+be)\b",
    re.IGNORECASE,
)

STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "of",
        "to",
        "in",
        "for",
        "and",
        "or",
        "i",
        "my",
        "me",
        "you",
        "your",
        "this",
        "that",
        "with",
        "on",
        "at",
        "by",
        "from",
        "as",
        "be",
    }
)

SOURCE_WEIGHTS = {
    "cli": 1.0,
    "discord": 0.95,
    "telegram": 0.95,
    "slack": 0.95,
    "whatsapp": 0.9,
    "signal": 0.9,
    "unknown": 0.8,
}


def _tokenize(text: str) -> set[str]:
    cleaned = text.lower().translate(str.maketrans("", "", string.punctuation))
    return {w for w in cleaned.split() if w and w not in STOPWORDS}


def _strip_priority_prefix(entry: str) -> str:
    if entry.startswith("[HIGH] "):
        return entry[len("[HIGH] ") :]
    m = re.match(r"^\[EPHEMERAL expires=\d{4}-\d{2}-\d{2}\] ", entry)
    if m:
        return entry[m.end() :]
    return entry


def _load_entries(path: Path) -> List[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]


def load_builtin_memory(hermes_home: Path) -> Dict[str, List[Dict[str, str]]]:
    mem_dir = hermes_home / "memories"
    out: Dict[str, List[Dict[str, str]]] = {"memory": [], "user": []}
    for target, filename in (("memory", "MEMORY.md"), ("user", "USER.md")):
        for raw_entry in _load_entries(mem_dir / filename):
            bare = _strip_priority_prefix(raw_entry).strip()
            if not bare:
                continue
            out[target].append({"raw": raw_entry, "bare": bare})
    return out


def _recent_since_date(days: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


def _parse_session_date(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)


def _recency_weight(session_date: str) -> float:
    dt = _parse_session_date(session_date)
    age_days = max((datetime.now(timezone.utc) - dt).days, 0)
    # 1.0 today, ~0.5 at 7 days, floor 0.25.
    return max(0.25, 1.0 / (1.0 + (age_days / 7.0)))


def _source_weight(source: str) -> float:
    src = (source or "unknown").strip().lower()
    return SOURCE_WEIGHTS.get(src, 0.8)


def _contradiction_strength(snippet: str) -> float:
    if STRONG_NEGATION_RE.search(snippet):
        return 1.0
    if NEGATION_RE.search(snippet):
        return 0.7
    return 0.0


def _is_ambiguous_snippet(snippet: str) -> bool:
    if AMBIGUOUS_RE.search(snippet):
        return True
    tokens = _tokenize(snippet)
    if len(tokens) < 4:
        return True
    if "?" in snippet and not NEGATION_RE.search(snippet):
        return True
    return False


def detect_memory_anomalies(
    metrics_conn,
    hermes_home: Path,
    *,
    days: int = 7,
    min_evidence: int = 2,
    min_overlap: float = 0.4,
    min_evidence_score: float = 1.25,
) -> List[Dict[str, Any]]:
    """Return stale-memory anomaly candidates from recent correction snippets."""
    memories = load_builtin_memory(hermes_home)
    if not memories["memory"] and not memories["user"]:
        return []

    rows = metrics_conn.execute(
        """
        SELECT correction_snippets, session_date, session_source
        FROM session_signals
        WHERE session_date >= ?
          AND correction_count > 0
        ORDER BY session_date DESC
        """,
        (_recent_since_date(days),),
    ).fetchall()

    snippets: List[Dict[str, Any]] = []
    for row in rows:
        try:
            vals = json.loads(row["correction_snippets"] or "[]")
        except Exception:
            vals = []
        for s in vals:
            if not isinstance(s, str) or not s.strip():
                continue
            snippet = s.strip()
            if _is_ambiguous_snippet(snippet):
                continue
            strength = _contradiction_strength(snippet)
            if strength <= 0:
                continue
            weight = (
                _recency_weight(str(row["session_date"]))
                * _source_weight(str(row["session_source"] or "unknown"))
                * strength
            )
            snippets.append(
                {
                    "text": snippet,
                    "weight": weight,
                }
            )

    if not snippets:
        return []

    results: List[Dict[str, Any]] = []
    for target in ("memory", "user"):
        for entry in memories[target]:
            entry_tokens = _tokenize(entry["bare"])
            if not entry_tokens:
                continue

            evidence: List[str] = []
            details: List[Dict[str, Any]] = []
            evidence_score = 0.0
            for snippet in snippets:
                snippet_tokens = _tokenize(snippet["text"])
                if not snippet_tokens:
                    continue
                overlap = len(entry_tokens & snippet_tokens) / max(len(entry_tokens), 1)
                if overlap < min_overlap:
                    continue
                score = overlap * float(snippet["weight"])
                evidence_score += score
                evidence.append(snippet["text"][:280])
                details.append(
                    {
                        "snippet": snippet["text"][:120],
                        "overlap": round(overlap, 3),
                        "weighted_score": round(score, 3),
                    }
                )

            if len(evidence) < min_evidence:
                continue
            if evidence_score < min_evidence_score:
                continue

            old_text = entry["bare"][:240]
            results.append(
                {
                    "target": target,
                    "old_text": old_text,
                    "entry_text": entry["bare"],
                    "evidence_count": len(evidence),
                    "weighted_evidence_score": round(evidence_score, 3),
                    "evidence_snippets": evidence[:6],
                    "evidence_details": details[:6],
                    "anomaly_type": "STALE_MEMORY",
                    "trigger_metric": (
                        f"contradiction_evidence={len(evidence)}, "
                        f"weighted_score={evidence_score:.2f}"
                    ),
                }
            )

    return results
