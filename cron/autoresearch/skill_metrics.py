"""
skill_metrics.py — Persistent store for autoresearch signal data.

Owns a dedicated SQLite database at ~/.hermes/autoresearch/skill_metrics.db
that is separate from state.db so autoresearch reads/writes never contend
with live sessions.

Three tables:
  session_signals   — one row per analysed session (raw extracted signals)
  skill_health      — one row per (skill, date) aggregated from session_signals
  autoresearch_patches — log of patches applied by the autoresearch loop
                         (used by Stage 3 regression watch)
"""

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home


def get_default_db_path() -> Path:
    return get_hermes_home() / "autoresearch" / "skill_metrics.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_signals (
    session_id        TEXT PRIMARY KEY,
    session_date      TEXT NOT NULL,
    total_tokens      INTEGER DEFAULT 0,
    tool_call_count   INTEGER DEFAULT 0,
    correction_count  INTEGER DEFAULT 0,
    completion_flag   INTEGER DEFAULT 0,
    skills_invoked    TEXT DEFAULT '[]',
    extracted_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_health (
    skill_name            TEXT    NOT NULL,
    health_date           TEXT    NOT NULL,
    invocation_count      INTEGER DEFAULT 0,
    avg_tokens            REAL    DEFAULT 0,
    correction_rate       REAL    DEFAULT 0,
    completion_rate       REAL    DEFAULT 0,
    in_session_patch_count INTEGER DEFAULT 0,
    PRIMARY KEY (skill_name, health_date)
);

CREATE TABLE IF NOT EXISTS autoresearch_patches (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name               TEXT    NOT NULL,
    patch_applied_at         TEXT    NOT NULL,
    patch_type               TEXT    NOT NULL,
    baseline_tokens          REAL,
    baseline_correction_rate REAL,
    baseline_completion_rate REAL,
    status                   TEXT    DEFAULT 'applied'
);
"""


def open_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialise) the skill_metrics database.

    Creates the file and parent directories if they do not yet exist.
    Returns a connection with row_factory set to sqlite3.Row.
    """
    path = db_path or get_default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# session_signals
# ---------------------------------------------------------------------------

def record_session_signal(conn: sqlite3.Connection, signal: Dict[str, Any]) -> None:
    """Insert or replace a session signal row.

    signal keys (all optional except session_id):
        session_id, session_date, total_tokens, tool_call_count,
        correction_count, completion_flag, skills_invoked (list[str])
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO session_signals
            (session_id, session_date, total_tokens, tool_call_count,
             correction_count, completion_flag, skills_invoked, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal["session_id"],
            signal.get("session_date", date.today().isoformat()),
            signal.get("total_tokens", 0),
            signal.get("tool_call_count", 0),
            signal.get("correction_count", 0),
            int(bool(signal.get("completion_flag", False))),
            json.dumps(signal.get("skills_invoked", [])),
            now,
        ),
    )
    conn.commit()


def get_session_signals(
    conn: sqlite3.Connection,
    since_date: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Return all session_signals, optionally filtered to on/after since_date (YYYY-MM-DD)."""
    if since_date:
        return conn.execute(
            "SELECT * FROM session_signals WHERE session_date >= ? ORDER BY session_date",
            (since_date,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM session_signals ORDER BY session_date"
    ).fetchall()


def already_extracted(conn: sqlite3.Connection, session_id: str) -> bool:
    """True if this session_id already has a row in session_signals."""
    row = conn.execute(
        "SELECT 1 FROM session_signals WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# skill_health aggregation
# ---------------------------------------------------------------------------

def upsert_skill_health(
    conn: sqlite3.Connection,
    skill_name: str,
    health_date: str,
    invocation_count: int,
    avg_tokens: float,
    correction_rate: float,
    completion_rate: float,
    in_session_patch_count: int = 0,
) -> None:
    """Insert or replace a skill_health row for (skill_name, health_date)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO skill_health
            (skill_name, health_date, invocation_count, avg_tokens,
             correction_rate, completion_rate, in_session_patch_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_name,
            health_date,
            invocation_count,
            avg_tokens,
            correction_rate,
            completion_rate,
            in_session_patch_count,
        ),
    )
    conn.commit()


def compute_and_store_skill_health(
    conn: sqlite3.Connection,
    for_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate session_signals for for_date into skill_health rows.

    Returns list of dicts describing what was written (one per skill).
    If for_date is None, uses today.
    """
    target_date = for_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT * FROM session_signals WHERE session_date = ?", (target_date,)
    ).fetchall()

    if not rows:
        return []

    # Build per-skill buckets from the sessions that mention each skill
    skill_buckets: Dict[str, List[Dict]] = {}
    for row in rows:
        skills = json.loads(row["skills_invoked"] or "[]")
        for skill in skills:
            skill_buckets.setdefault(skill, []).append(dict(row))

    results = []
    for skill_name, sessions in skill_buckets.items():
        n = len(sessions)
        avg_tokens = sum(s["total_tokens"] for s in sessions) / n
        correction_rate = sum(s["correction_count"] > 0 for s in sessions) / n
        completion_rate = sum(s["completion_flag"] for s in sessions) / n

        upsert_skill_health(
            conn, skill_name, target_date, n, avg_tokens,
            correction_rate, completion_rate,
        )
        results.append({
            "skill_name": skill_name,
            "health_date": target_date,
            "invocation_count": n,
            "avg_tokens": avg_tokens,
            "correction_rate": correction_rate,
            "completion_rate": completion_rate,
        })
    return results


def get_skill_health_summary(
    conn: sqlite3.Connection,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """Return 7-day (or N-day) rolling aggregate per skill.

    Queries skill_health for the last `days` dates and groups by skill_name.
    Returns list of dicts sorted by correction_rate descending (worst first).
    """
    rows = conn.execute(
        """
        SELECT
            skill_name,
            SUM(invocation_count)                                        AS total_invocations,
            AVG(avg_tokens)                                              AS avg_tokens,
            SUM(correction_rate * invocation_count)
                / NULLIF(SUM(invocation_count), 0)                      AS correction_rate,
            SUM(completion_rate * invocation_count)
                / NULLIF(SUM(invocation_count), 0)                      AS completion_rate,
            SUM(in_session_patch_count)                                  AS patch_count
        FROM skill_health
        WHERE health_date >= date('now', ?)
        GROUP BY skill_name
        ORDER BY correction_rate DESC
        """,
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]
