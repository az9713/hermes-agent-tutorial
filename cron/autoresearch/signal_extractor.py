"""
signal_extractor.py — Extract session outcome signals from state.db.

Reads sessions and messages tables from the live Hermes state database and
produces a list of SessionSignal dicts, one per session, containing:

  session_id       — unique session identifier
  session_date     — YYYY-MM-DD of session start
  total_tokens     — input_tokens + output_tokens from sessions table
  tool_call_count  — tool_call_count from sessions table
  correction_count — number of user turns containing correction language
  completion_flag  — 1 if session ended naturally, 0 otherwise
  skills_invoked   — list of skill directory names present in system_prompt

Correction detection uses regex on user-role messages. It catches both
explicit corrections ("no", "wrong", "try again") and implicit goal
rephrase (same user goal stated twice within 2 turns, detected via
shared significant words).

Skill detection reads skill directory names from HERMES_HOME/skills/ and
checks each name against the session's stored system_prompt. This is a
heuristic — skills whose names appear in the system_prompt are considered
active. Reliable per-session skill attribution will require LLM
classification in Stage 2.
"""

import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Correction detection
# ---------------------------------------------------------------------------

# Patterns matched against user-role message content (case-insensitive).
# Designed to catch clear correction signals without false-positives on
# ordinary conversation ("no problem", "that's not what I was asking").
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

# Patterns indicating natural session completion in the user's final message.
_COMPLETION_PATTERNS = re.compile(
    r"\b(thanks|thank\s+you|perfect|done|got\s+it|great|awesome|looks?\s+good"
    r"|that'?s\s+(it|all)|all\s+set|that\s+worked|works?(\s+great)?)\b",
    re.IGNORECASE,
)

# End reasons that indicate a natural (user-initiated) session close.
_NATURAL_END_REASONS = {"cli_close", "user_quit"}


def _count_corrections(messages: List[sqlite3.Row]) -> int:
    """Count user turns that contain explicit correction language."""
    count = 0
    for msg in messages:
        if msg["role"] != "user":
            continue
        content = msg["content"] or ""
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        # content may be a JSON string for tool results — skip those
        if content.startswith("[") or content.startswith("{"):
            continue
        if _CORRECTION_PATTERNS.search(content):
            count += 1
    return count


def _check_completion(session_row: sqlite3.Row, messages: List[sqlite3.Row]) -> bool:
    """Return True if the session appears to have ended naturally.

    Two criteria (either is sufficient):
      1. end_reason is in _NATURAL_END_REASONS
      2. Last user message contains a completion acknowledgment
    """
    end_reason = session_row["end_reason"] or ""
    if end_reason in _NATURAL_END_REASONS:
        return True

    # Check last user message for acknowledgment patterns
    user_msgs = [m for m in messages if m["role"] == "user"]
    if user_msgs:
        last_content = user_msgs[-1]["content"] or ""
        if isinstance(last_content, bytes):
            last_content = last_content.decode("utf-8", errors="replace")
        if _COMPLETION_PATTERNS.search(last_content):
            return True

    return False


# ---------------------------------------------------------------------------
# Skill detection
# ---------------------------------------------------------------------------

def _get_known_skill_names(hermes_home: Optional[Path] = None) -> List[str]:
    """Return a list of skill directory names from HERMES_HOME/skills/.

    Only returns directory-level names (e.g. "git-workflow", "web-search"),
    not category directories.  A directory is considered a skill if it
    contains a SKILL.md file.
    """
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
    """Return skill names from known_skill_names found in system_prompt.

    Matches whole skill names as word-boundary substrings.  For example,
    "git-workflow" matches "git-workflow" in the prompt but not "git".
    """
    if not system_prompt or not known_skill_names:
        return []
    found = []
    for name in known_skill_names:
        # Escape hyphens/dots for regex; match as word/token boundary
        pattern = re.escape(name)
        if re.search(pattern, system_prompt, re.IGNORECASE):
            found.append(name)
    return found


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_signals(
    state_db_path: Optional[Path] = None,
    since_hours: int = 24,
    hermes_home: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Extract session signals from state.db for sessions in the last since_hours.

    Returns a list of SessionSignal dicts. Returns an empty list if state.db
    does not exist or contains no sessions in the time window.

    Args:
        state_db_path: Path to state.db. Defaults to HERMES_HOME/state.db.
        since_hours:   How many hours back to look. Default 24.
        hermes_home:   Override HERMES_HOME for skill discovery. Defaults to
                       get_hermes_home().
    """
    home = hermes_home or get_hermes_home()
    db_path = state_db_path or (home / "state.db")

    if not db_path.exists():
        logger.info("state.db not found at %s — no signals to extract", db_path)
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
            completion_flag = _check_completion(session, messages)
            skills_invoked = _detect_skills_in_prompt(
                session["system_prompt"] or "", known_skills
            )

            signals.append({
                "session_id": session_id,
                "session_date": session_date,
                "total_tokens": total_tokens,
                "tool_call_count": tool_call_count,
                "correction_count": correction_count,
                "completion_flag": completion_flag,
                "skills_invoked": skills_invoked,
            })

    except Exception as exc:
        logger.error("Error extracting signals from state.db: %s", exc)
    finally:
        conn.close()

    logger.info(
        "Extracted signals from %d sessions (since_hours=%d)",
        len(signals), since_hours,
    )
    return signals
