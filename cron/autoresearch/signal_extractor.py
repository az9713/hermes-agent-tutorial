"""
signal_extractor.py -- Extract session outcome signals from state.db.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import string
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Correction detection
# ---------------------------------------------------------------------------

_CORRECTION_PATTERNS = re.compile(
    r"\b("
    r"that'?s\s+(wrong|incorrect|not\s+right|not\s+what\s+I)"
    r"|try\s+again"
    r"|not\s+what\s+I\s+(meant|asked|wanted|said)"
    r"|that\s+(didn'?t\s+work|is\s+wrong|is\s+incorrect)"
    r"|you\s+(misunderstood|got\s+it\s+wrong)"
    r"|incorrect"
    r"|start\s+over"
    r"|that'?s\s+not\s+(right|correct)"
    r")\b",
    re.IGNORECASE,
)

_CORRECTION_LABEL_PATTERNS = {
    "explicit_wrong": re.compile(
        r"\b(wrong|incorrect|not\s+right|not\s+what\s+i)\b", re.IGNORECASE
    ),
    "retry_request": re.compile(r"\b(try\s+again|redo|re-run)\b", re.IGNORECASE),
    "misunderstanding": re.compile(
        r"\b(misunderstood|got\s+it\s+wrong|not\s+what\s+i\s+(meant|asked|wanted|said))\b",
        re.IGNORECASE,
    ),
    "reset_request": re.compile(r"\b(start\s+over|from\s+scratch)\b", re.IGNORECASE),
    "uncertainty": re.compile(
        r"\b(maybe|might|i\s+think|not\s+sure|possibly|perhaps)\b",
        re.IGNORECASE,
    ),
}

_COMPLETION_PATTERNS = re.compile(
    r"\b(thanks|thank\s+you|perfect|done|got\s+it|great|awesome|looks?\s+good"
    r"|that'?s\s+(it|all)|all\s+set|that\s+worked|works?(\s+great)?)\b",
    re.IGNORECASE,
)

_NATURAL_END_REASONS = {"cli_close", "user_quit"}

_NEGATION_RE = re.compile(
    r"\b(no|not|never|wrong|incorrect|misunderstood)\b", re.IGNORECASE
)

_ATTR_STOPWORDS = frozenset(
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


def _normalize_content(msg: sqlite3.Row) -> str:
    content = msg["content"] or ""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return content


def _count_corrections(messages: List[sqlite3.Row]) -> int:
    count = 0
    for msg in messages:
        if msg["role"] != "user":
            continue
        content = _normalize_content(msg)
        if content.startswith("[") or content.startswith("{"):
            continue
        if _CORRECTION_PATTERNS.search(content):
            count += 1
    return count


def _classify_correction_labels(text: str) -> List[str]:
    labels: List[str] = []
    for label, pat in _CORRECTION_LABEL_PATTERNS.items():
        if pat.search(text):
            labels.append(label)
    if not labels and _CORRECTION_PATTERNS.search(text):
        labels.append("generic_correction")
    return labels


def _extract_correction_snippets(
    messages: List[sqlite3.Row],
    max_snippets: int = 5,
) -> List[str]:
    snippets: List[str] = []
    for msg in messages:
        if msg["role"] != "user":
            continue
        content = _normalize_content(msg)
        if content.startswith("[") or content.startswith("{"):
            continue
        if not _CORRECTION_PATTERNS.search(content):
            continue
        clean = " ".join(content.strip().split())
        if clean:
            snippets.append(clean[:280])
        if len(snippets) >= max_snippets:
            break
    return snippets


def _extract_correction_labels(
    messages: List[sqlite3.Row],
    max_labels: int = 8,
) -> List[str]:
    labels: List[str] = []
    seen = set()
    for msg in messages:
        if msg["role"] != "user":
            continue
        content = _normalize_content(msg)
        if content.startswith("[") or content.startswith("{"):
            continue
        for label in _classify_correction_labels(content):
            if label in seen:
                continue
            seen.add(label)
            labels.append(label)
            if len(labels) >= max_labels:
                return labels
    return labels


def _compute_correction_intensity(
    correction_count: int,
    correction_labels: List[str],
) -> float:
    if correction_count <= 0:
        return 0.0
    base = min(1.0, correction_count / 3.0)
    label_bonus = 0.0
    if "explicit_wrong" in correction_labels:
        label_bonus += 0.2
    if "misunderstanding" in correction_labels:
        label_bonus += 0.2
    if "reset_request" in correction_labels:
        label_bonus += 0.2
    return min(1.0, base + label_bonus)


def _check_completion(session_row: sqlite3.Row, messages: List[sqlite3.Row]) -> bool:
    end_reason = session_row["end_reason"] or ""
    if end_reason in _NATURAL_END_REASONS:
        return True
    user_msgs = [m for m in messages if m["role"] == "user"]
    if user_msgs:
        last_content = _normalize_content(user_msgs[-1])
        if _COMPLETION_PATTERNS.search(last_content):
            return True
    return False


def _completion_confidence(
    session_row: sqlite3.Row,
    messages: List[sqlite3.Row],
    correction_count: int,
) -> float:
    end_reason = session_row["end_reason"] or ""
    if end_reason in _NATURAL_END_REASONS:
        return 1.0
    user_msgs = [m for m in messages if m["role"] == "user"]
    if user_msgs:
        last_content = _normalize_content(user_msgs[-1])
        if _COMPLETION_PATTERNS.search(last_content):
            return 0.8
    if correction_count == 0:
        return 0.4
    return 0.15


# ---------------------------------------------------------------------------
# Skill detection + attribution
# ---------------------------------------------------------------------------

def _get_known_skill_names(hermes_home: Optional[Path] = None) -> List[str]:
    skills_root = (hermes_home or get_hermes_home()) / "skills"
    if not skills_root.exists():
        return []
    names = []
    for skill_md in skills_root.rglob("SKILL.md"):
        names.append(skill_md.parent.name)
    return names


def _detect_skills_in_prompt(
    system_prompt: str,
    known_skill_names: List[str],
) -> List[str]:
    if not system_prompt or not known_skill_names:
        return []
    found = []
    for name in known_skill_names:
        if re.search(re.escape(name), system_prompt, re.IGNORECASE):
            found.append(name)
    return found


def _tokenize(text: str) -> set[str]:
    cleaned = text.lower().translate(str.maketrans("", "", string.punctuation))
    return {w for w in cleaned.split() if w and w not in _ATTR_STOPWORDS}


def _estimate_skill_attribution(
    skills_invoked: List[str],
    correction_snippets: List[str],
    correction_intensity: float,
) -> Dict[str, float]:
    if not skills_invoked:
        return {}
    combined = " ".join(correction_snippets).lower()
    out: Dict[str, float] = {}
    for skill in skills_invoked:
        tokens = [t for t in re.split(r"[-_./\s]+", skill.lower()) if t]
        keyword_hit = any(tok in combined for tok in tokens)
        conf = (0.65 * correction_intensity) + (0.35 if keyword_hit else 0.1)
        out[skill] = round(min(1.0, max(0.0, conf)), 3)
    return out


def _load_memory_entries(hermes_home: Path) -> Dict[str, List[str]]:
    from tools.memory_tool import ENTRY_DELIMITER

    mem_dir = hermes_home / "memories"
    out: Dict[str, List[str]] = {"memory": [], "user": []}
    for target, filename in (("memory", "MEMORY.md"), ("user", "USER.md")):
        path = mem_dir / filename
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        out[target] = [x.strip() for x in raw.split(ENTRY_DELIMITER) if x.strip()]
    return out


def _estimate_memory_attribution(
    correction_snippets: List[str],
    hermes_home: Path,
) -> Dict[str, float]:
    entries = _load_memory_entries(hermes_home)
    if not correction_snippets:
        return {"memory": 0.0, "user": 0.0}

    out = {"memory": 0.0, "user": 0.0}
    for target in ("memory", "user"):
        max_score = 0.0
        entry_texts = entries.get(target, [])
        if not entry_texts:
            out[target] = 0.0
            continue
        for snippet in correction_snippets:
            if not _NEGATION_RE.search(snippet):
                continue
            snippet_tokens = _tokenize(snippet)
            if not snippet_tokens:
                continue
            for entry in entry_texts:
                entry_tokens = _tokenize(entry)
                if not entry_tokens:
                    continue
                overlap = len(entry_tokens & snippet_tokens) / max(len(entry_tokens), 1)
                max_score = max(max_score, overlap)
        out[target] = round(min(1.0, max_score), 3)
    return out


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_signals(
    state_db_path: Optional[Path] = None,
    since_hours: int = 24,
    hermes_home: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Extract session signals from state.db for sessions in the last since_hours."""
    home = hermes_home or get_hermes_home()
    db_path = state_db_path or (home / "state.db")
    if not db_path.exists():
        logger.info("state.db not found at %s - no signals to extract", db_path)
        return []

    since_ts = time.time() - since_hours * 3600
    known_skills = _get_known_skill_names(home)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        logger.error("Cannot open state.db at %s: %s", db_path, exc)
        return []

    signals: List[Dict[str, Any]] = []
    try:
        sessions = conn.execute(
            """
            SELECT id, source, input_tokens, output_tokens, tool_call_count,
                   end_reason, started_at, ended_at, system_prompt
            FROM sessions
            WHERE started_at >= ?
            ORDER BY started_at
            """,
            (since_ts,),
        ).fetchall()

        for session in sessions:
            session_id = session["id"]
            session_date = datetime.fromtimestamp(
                session["started_at"], tz=timezone.utc
            ).strftime("%Y-%m-%d")
            total_tokens = (session["input_tokens"] or 0) + (session["output_tokens"] or 0)
            tool_call_count = session["tool_call_count"] or 0
            messages = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

            correction_count = _count_corrections(messages)
            correction_snippets = _extract_correction_snippets(messages)
            correction_labels = _extract_correction_labels(messages)
            correction_intensity = _compute_correction_intensity(
                correction_count=correction_count,
                correction_labels=correction_labels,
            )
            completion_flag = _check_completion(session, messages)
            completion_confidence = _completion_confidence(
                session_row=session,
                messages=messages,
                correction_count=correction_count,
            )
            skills_invoked = _detect_skills_in_prompt(
                session["system_prompt"] or "", known_skills
            )
            skill_attribution = _estimate_skill_attribution(
                skills_invoked=skills_invoked,
                correction_snippets=correction_snippets,
                correction_intensity=correction_intensity,
            )
            memory_attribution = _estimate_memory_attribution(
                correction_snippets=correction_snippets,
                hermes_home=home,
            )

            signals.append(
                {
                    "session_id": session_id,
                    "session_date": session_date,
                    "total_tokens": total_tokens,
                    "tool_call_count": tool_call_count,
                    "correction_count": correction_count,
                    "correction_snippets": correction_snippets,
                    "correction_labels": correction_labels,
                    "correction_intensity": correction_intensity,
                    "completion_flag": completion_flag,
                    "completion_confidence": completion_confidence,
                    "skills_invoked": skills_invoked,
                    "session_source": session["source"] or "",
                    "skill_attribution": skill_attribution,
                    "memory_attribution": memory_attribution,
                }
            )
    except Exception as exc:
        logger.error("Error extracting signals from state.db: %s", exc)
    finally:
        conn.close()

    logger.info(
        "Extracted signals from %d sessions (since_hours=%d)",
        len(signals),
        since_hours,
    )
    return signals
