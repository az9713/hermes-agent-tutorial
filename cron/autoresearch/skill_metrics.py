"""
skill_metrics.py -- Persistent store for autoresearch signal data.

Owns a dedicated SQLite database at ~/.hermes/autoresearch/skill_metrics.db
that is separate from state.db so autoresearch reads/writes never contend
with live sessions.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from hermes_constants import get_hermes_home


def get_default_db_path() -> Path:
    return get_hermes_home() / "autoresearch" / "skill_metrics.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_signals (
    session_id           TEXT PRIMARY KEY,
    session_date         TEXT NOT NULL,
    total_tokens         INTEGER DEFAULT 0,
    tool_call_count      INTEGER DEFAULT 0,
    correction_count     INTEGER DEFAULT 0,
    correction_snippets  TEXT DEFAULT '[]',
    correction_labels    TEXT DEFAULT '[]',
    correction_intensity REAL DEFAULT 0,
    completion_flag      INTEGER DEFAULT 0,
    completion_confidence REAL DEFAULT 0,
    skills_invoked       TEXT DEFAULT '[]',
    session_source       TEXT DEFAULT '',
    skill_attribution    TEXT DEFAULT '{}',
    memory_attribution   TEXT DEFAULT '{}',
    extracted_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_health (
    skill_name                  TEXT    NOT NULL,
    health_date                 TEXT    NOT NULL,
    invocation_count            INTEGER DEFAULT 0,
    avg_tokens                  REAL    DEFAULT 0,
    avg_tool_calls              REAL    DEFAULT 0,
    correction_rate             REAL    DEFAULT 0,
    completion_rate             REAL    DEFAULT 0,
    avg_correction_intensity    REAL    DEFAULT 0,
    avg_completion_confidence   REAL    DEFAULT 0,
    avg_skill_causal_confidence REAL    DEFAULT 0,
    in_session_patch_count      INTEGER DEFAULT 0,
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
    old_string               TEXT,
    new_string               TEXT,
    status                   TEXT    DEFAULT 'applied'
);

CREATE TABLE IF NOT EXISTS autoresearch_memory_updates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target            TEXT    NOT NULL,
    action            TEXT    NOT NULL,
    old_text          TEXT    NOT NULL,
    new_content       TEXT,
    reason            TEXT    DEFAULT '',
    confidence        REAL    DEFAULT 0,
    evidence_count    INTEGER DEFAULT 0,
    evidence_score    REAL    DEFAULT 0,
    first_seen_at     TEXT    NOT NULL,
    apply_after       TEXT    NOT NULL,
    last_validated_at TEXT,
    status            TEXT    NOT NULL DEFAULT 'proposed',
    applied_at        TEXT,
    error             TEXT
);

CREATE TABLE IF NOT EXISTS autoresearch_holdout_cases (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name        TEXT    NOT NULL,
    task_text         TEXT    NOT NULL,
    source_session_id TEXT,
    source_date       TEXT,
    created_at        TEXT    NOT NULL,
    used_in_eval      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS autoresearch_eval_runs (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name               TEXT    NOT NULL,
    anomaly_type             TEXT    NOT NULL,
    status                   TEXT    NOT NULL,
    self_play_token_delta    REAL,
    self_play_quality_delta  REAL,
    holdout_quality_delta    REAL,
    holdout_pass             INTEGER DEFAULT 0,
    rubric_pass_rate_old     REAL,
    rubric_pass_rate_new     REAL,
    dual_judge_disagreement  INTEGER DEFAULT 0,
    evaluated_at             TEXT    NOT NULL
);
"""


def open_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialise) the skill_metrics database."""
    path = db_path or get_default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    _ensure_columns(conn)
    conn.commit()
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Best-effort in-place migrations for older databases."""
    # session_signals
    _add_column_if_missing(conn, "session_signals", "correction_snippets", "TEXT DEFAULT '[]'")
    _add_column_if_missing(conn, "session_signals", "correction_labels", "TEXT DEFAULT '[]'")
    _add_column_if_missing(conn, "session_signals", "correction_intensity", "REAL DEFAULT 0")
    _add_column_if_missing(conn, "session_signals", "completion_confidence", "REAL DEFAULT 0")
    _add_column_if_missing(conn, "session_signals", "session_source", "TEXT DEFAULT ''")
    _add_column_if_missing(conn, "session_signals", "skill_attribution", "TEXT DEFAULT '{}'")
    _add_column_if_missing(conn, "session_signals", "memory_attribution", "TEXT DEFAULT '{}'")

    # skill_health
    _add_column_if_missing(conn, "skill_health", "avg_tool_calls", "REAL DEFAULT 0")
    _add_column_if_missing(conn, "skill_health", "avg_correction_intensity", "REAL DEFAULT 0")
    _add_column_if_missing(conn, "skill_health", "avg_completion_confidence", "REAL DEFAULT 0")
    _add_column_if_missing(conn, "skill_health", "avg_skill_causal_confidence", "REAL DEFAULT 0")

    # autoresearch_patches
    _add_column_if_missing(conn, "autoresearch_patches", "old_string", "TEXT")
    _add_column_if_missing(conn, "autoresearch_patches", "new_string", "TEXT")

    # autoresearch_memory_updates
    _add_column_if_missing(conn, "autoresearch_memory_updates", "evidence_score", "REAL DEFAULT 0")


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
) -> None:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(c["name"] == column for c in cols):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# ---------------------------------------------------------------------------
# session_signals
# ---------------------------------------------------------------------------

def record_session_signal(conn: sqlite3.Connection, signal: Dict[str, Any]) -> None:
    """Insert or replace a session signal row."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO session_signals
            (session_id, session_date, total_tokens, tool_call_count,
             correction_count, correction_snippets, correction_labels,
             correction_intensity, completion_flag, completion_confidence,
             skills_invoked, session_source, skill_attribution, memory_attribution, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal["session_id"],
            signal.get("session_date", date.today().isoformat()),
            signal.get("total_tokens", 0),
            signal.get("tool_call_count", 0),
            signal.get("correction_count", 0),
            json.dumps(signal.get("correction_snippets", [])),
            json.dumps(signal.get("correction_labels", [])),
            float(signal.get("correction_intensity", 0.0)),
            int(bool(signal.get("completion_flag", False))),
            float(signal.get("completion_confidence", 0.0)),
            json.dumps(signal.get("skills_invoked", [])),
            str(signal.get("session_source", "")),
            json.dumps(signal.get("skill_attribution", {})),
            json.dumps(signal.get("memory_attribution", {})),
            now,
        ),
    )
    conn.commit()


def get_session_signals(
    conn: sqlite3.Connection,
    since_date: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Return all session_signals, optionally filtered to on/after since_date."""
    if since_date:
        return conn.execute(
            "SELECT * FROM session_signals WHERE session_date >= ? ORDER BY session_date",
            (since_date,),
        ).fetchall()
    return conn.execute("SELECT * FROM session_signals ORDER BY session_date").fetchall()


def already_extracted(conn: sqlite3.Connection, session_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM session_signals WHERE session_id = ?",
        (session_id,),
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
    avg_tool_calls: float = 0.0,
    avg_correction_intensity: float = 0.0,
    avg_completion_confidence: float = 0.0,
    avg_skill_causal_confidence: float = 0.0,
) -> None:
    """Insert or replace a skill_health row for (skill_name, health_date)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO skill_health
            (skill_name, health_date, invocation_count, avg_tokens, avg_tool_calls,
             correction_rate, completion_rate, avg_correction_intensity,
             avg_completion_confidence, avg_skill_causal_confidence, in_session_patch_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_name,
            health_date,
            invocation_count,
            avg_tokens,
            avg_tool_calls,
            correction_rate,
            completion_rate,
            avg_correction_intensity,
            avg_completion_confidence,
            avg_skill_causal_confidence,
            in_session_patch_count,
        ),
    )
    conn.commit()


def _safe_json_loads(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw or json.dumps(fallback))
    except Exception:
        return fallback


def compute_and_store_skill_health(
    conn: sqlite3.Connection,
    for_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate session_signals for for_date into skill_health rows."""
    target_date = for_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM session_signals WHERE session_date = ?",
        (target_date,),
    ).fetchall()
    if not rows:
        return []

    skill_buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        skills = _safe_json_loads(row["skills_invoked"], [])
        for skill in skills:
            skill_buckets.setdefault(skill, []).append(dict(row))

    results: List[Dict[str, Any]] = []
    for skill_name, sessions in skill_buckets.items():
        n = len(sessions)
        avg_tokens = sum(float(s.get("total_tokens", 0) or 0) for s in sessions) / n
        avg_tool_calls = sum(float(s.get("tool_call_count", 0) or 0) for s in sessions) / n
        correction_rate = sum(int((s.get("correction_count", 0) or 0) > 0) for s in sessions) / n
        completion_rate = sum(int(bool(s.get("completion_flag", 0))) for s in sessions) / n
        avg_correction_intensity = (
            sum(float(s.get("correction_intensity", 0) or 0) for s in sessions) / n
        )
        avg_completion_confidence = (
            sum(float(s.get("completion_confidence", 0) or 0) for s in sessions) / n
        )
        causal_vals: List[float] = []
        for s in sessions:
            attribution = _safe_json_loads(s.get("skill_attribution"), {})
            val = attribution.get(skill_name)
            if val is not None:
                try:
                    causal_vals.append(float(val))
                except Exception:
                    pass
        avg_skill_causal_confidence = (
            sum(causal_vals) / len(causal_vals) if causal_vals else 0.0
        )

        upsert_skill_health(
            conn=conn,
            skill_name=skill_name,
            health_date=target_date,
            invocation_count=n,
            avg_tokens=avg_tokens,
            avg_tool_calls=avg_tool_calls,
            correction_rate=correction_rate,
            completion_rate=completion_rate,
            avg_correction_intensity=avg_correction_intensity,
            avg_completion_confidence=avg_completion_confidence,
            avg_skill_causal_confidence=avg_skill_causal_confidence,
        )
        results.append(
            {
                "skill_name": skill_name,
                "health_date": target_date,
                "invocation_count": n,
                "avg_tokens": avg_tokens,
                "avg_tool_calls": avg_tool_calls,
                "correction_rate": correction_rate,
                "completion_rate": completion_rate,
                "avg_correction_intensity": avg_correction_intensity,
                "avg_completion_confidence": avg_completion_confidence,
                "avg_skill_causal_confidence": avg_skill_causal_confidence,
            }
        )
    return results


# ---------------------------------------------------------------------------
# autoresearch_patches
# ---------------------------------------------------------------------------

def record_autoresearch_patch(
    conn: sqlite3.Connection,
    skill_name: str,
    patch_type: str,
    baseline_correction_rate: float,
    baseline_completion_rate: float,
    baseline_tokens: float = 0.0,
    old_string: str = "",
    new_string: str = "",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO autoresearch_patches
            (skill_name, patch_applied_at, patch_type,
             baseline_tokens, baseline_correction_rate, baseline_completion_rate,
             old_string, new_string, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied')
        """,
        (
            skill_name,
            now,
            patch_type,
            baseline_tokens,
            baseline_correction_rate,
            baseline_completion_rate,
            old_string,
            new_string,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_applied_patches(
    conn: sqlite3.Connection,
    since_ts: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if since_ts:
        rows = conn.execute(
            "SELECT * FROM autoresearch_patches WHERE status='applied' AND patch_applied_at >= ?",
            (since_ts,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM autoresearch_patches WHERE status='applied'"
        ).fetchall()
    return [dict(r) for r in rows]


def update_patch_status(
    conn: sqlite3.Connection,
    patch_id: int,
    status: str,
) -> None:
    conn.execute(
        "UPDATE autoresearch_patches SET status=? WHERE id=?",
        (status, patch_id),
    )
    conn.commit()


def get_skill_health_summary(
    conn: sqlite3.Connection,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """Return rolling aggregate per skill."""
    rows = conn.execute(
        """
        SELECT
            skill_name,
            SUM(invocation_count)                                        AS total_invocations,
            AVG(avg_tokens)                                              AS avg_tokens,
            AVG(avg_tool_calls)                                          AS avg_tool_calls,
            AVG(avg_skill_causal_confidence)                             AS avg_skill_causal_confidence,
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


# ---------------------------------------------------------------------------
# holdout + eval tracking
# ---------------------------------------------------------------------------

def _recent_since_date(days: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


def build_and_store_holdout_cases(
    conn: sqlite3.Connection,
    skill_name: str,
    *,
    days: int = 30,
    limit: int = 20,
    exclude_texts: Optional[Sequence[str]] = None,
) -> List[str]:
    """Build holdout tasks from recent session signals and persist them."""
    excluded = {x.strip() for x in (exclude_texts or []) if x and x.strip()}
    rows = get_session_signals(conn, since_date=_recent_since_date(days))

    candidates: List[Dict[str, str]] = []
    seen = set(excluded)
    for row in rows:
        skills = _safe_json_loads(row["skills_invoked"], [])
        if skill_name not in skills:
            continue
        snippets = _safe_json_loads(row["correction_snippets"], [])
        for snippet in snippets:
            if not isinstance(snippet, str):
                continue
            task = snippet.strip()
            if not task or task in seen:
                continue
            seen.add(task)
            candidates.append(
                {
                    "task_text": task,
                    "source_session_id": str(row["session_id"]),
                    "source_date": str(row["session_date"]),
                }
            )
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    now = datetime.now(timezone.utc).isoformat()
    for c in candidates:
        exists = conn.execute(
            """
            SELECT 1 FROM autoresearch_holdout_cases
            WHERE skill_name = ? AND task_text = ?
            LIMIT 1
            """,
            (skill_name, c["task_text"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO autoresearch_holdout_cases
                (skill_name, task_text, source_session_id, source_date, created_at, used_in_eval)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                skill_name,
                c["task_text"],
                c.get("source_session_id"),
                c.get("source_date"),
                now,
            ),
        )
    conn.commit()
    return [c["task_text"] for c in candidates]


def mark_holdout_cases_used(
    conn: sqlite3.Connection,
    skill_name: str,
    tasks: Sequence[str],
) -> None:
    if not tasks:
        return
    for t in tasks:
        conn.execute(
            """
            UPDATE autoresearch_holdout_cases
            SET used_in_eval = 1
            WHERE skill_name = ? AND task_text = ?
            """,
            (skill_name, t),
        )
    conn.commit()


def record_eval_run(
    conn: sqlite3.Connection,
    *,
    skill_name: str,
    anomaly_type: str,
    status: str,
    self_play_token_delta: float,
    self_play_quality_delta: float,
    holdout_quality_delta: float,
    holdout_pass: bool,
    rubric_pass_rate_old: float,
    rubric_pass_rate_new: float,
    dual_judge_disagreement: bool,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO autoresearch_eval_runs
            (skill_name, anomaly_type, status, self_play_token_delta, self_play_quality_delta,
             holdout_quality_delta, holdout_pass, rubric_pass_rate_old, rubric_pass_rate_new,
             dual_judge_disagreement, evaluated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_name,
            anomaly_type,
            status,
            self_play_token_delta,
            self_play_quality_delta,
            holdout_quality_delta,
            int(holdout_pass),
            rubric_pass_rate_old,
            rubric_pass_rate_new,
            int(dual_judge_disagreement),
            now,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_operator_confidence_metrics(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
) -> Dict[str, float]:
    """Compute rolling operator confidence KPIs."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    patch_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM autoresearch_patches
        WHERE patch_applied_at >= ?
        GROUP BY status
        """,
        (since,),
    ).fetchall()
    patch_counts = {r["status"]: int(r["n"]) for r in patch_rows}
    stable = patch_counts.get("stable", 0)
    rolled_back = patch_counts.get("rolled_back", 0)
    needs_review = patch_counts.get("needs_review", 0)
    applied = patch_counts.get("applied", 0)

    stability_den = stable + rolled_back + needs_review
    patch_stability_ratio = (stable / stability_den) if stability_den else 0.0
    acceptance_to_regression_ratio = (
        applied / max(rolled_back + needs_review, 1)
    )

    mem_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM autoresearch_memory_updates
        WHERE first_seen_at >= ?
        GROUP BY status
        """,
        (since,),
    ).fetchall()
    mem_counts = {r["status"]: int(r["n"]) for r in mem_rows}
    mem_applied = mem_counts.get("applied", 0)
    mem_bad = mem_counts.get("needs_review", 0) + mem_counts.get("failed", 0)
    memory_precision_proxy = mem_applied / max(mem_applied + mem_bad, 1)

    holdout = conn.execute(
        """
        SELECT
            SUM(CASE WHEN holdout_pass = 1 THEN 1 ELSE 0 END) AS pass_n,
            COUNT(*) AS total_n
        FROM autoresearch_eval_runs
        WHERE evaluated_at >= ?
        """,
        (since,),
    ).fetchone()
    holdout_pass = int(holdout["pass_n"] or 0)
    holdout_total = int(holdout["total_n"] or 0)
    holdout_pass_rate = holdout_pass / holdout_total if holdout_total else 0.0

    return {
        "window_days": float(days),
        "patch_stability_ratio": patch_stability_ratio,
        "acceptance_to_regression_ratio": acceptance_to_regression_ratio,
        "memory_precision_proxy": memory_precision_proxy,
        "holdout_pass_rate": holdout_pass_rate,
        "patch_count_stable": float(stable),
        "patch_count_rolled_back": float(rolled_back),
        "patch_count_needs_review": float(needs_review),
        "patch_count_applied": float(applied),
    }


# ---------------------------------------------------------------------------
# autoresearch_memory_updates
# ---------------------------------------------------------------------------

DEFAULT_MEMORY_APPLY_DELAY_HOURS = 24
OPEN_MEMORY_UPDATE_STATUSES = ("proposed", "pending_revalidation")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_after_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def upsert_memory_update_proposal(
    conn: sqlite3.Connection,
    *,
    target: str,
    action: str,
    old_text: str,
    new_content: str,
    reason: str,
    confidence: float,
    evidence_count: int,
    evidence_score: float = 0.0,
    apply_delay_hours: int = DEFAULT_MEMORY_APPLY_DELAY_HOURS,
) -> int:
    """Create or refresh a memory update proposal."""
    existing = conn.execute(
        """
        SELECT id
        FROM autoresearch_memory_updates
        WHERE target = ? AND action = ? AND old_text = ? AND new_content = ?
          AND status IN (?, ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (target, action, old_text, new_content, *OPEN_MEMORY_UPDATE_STATUSES),
    ).fetchone()
    now = _utc_now_iso()

    if existing:
        conn.execute(
            """
            UPDATE autoresearch_memory_updates
            SET reason = ?, confidence = ?, evidence_count = ?, evidence_score = ?,
                last_validated_at = ?, status = 'proposed', error = NULL
            WHERE id = ?
            """,
            (reason, confidence, evidence_count, evidence_score, now, existing["id"]),
        )
        conn.commit()
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO autoresearch_memory_updates
            (target, action, old_text, new_content, reason, confidence, evidence_count,
             evidence_score, first_seen_at, apply_after, last_validated_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed')
        """,
        (
            target,
            action,
            old_text,
            new_content,
            reason,
            confidence,
            evidence_count,
            evidence_score,
            now,
            _iso_after_hours(apply_delay_hours),
            now,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_memory_updates(
    conn: sqlite3.Connection,
    statuses: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        rows = conn.execute(
            f"""
            SELECT * FROM autoresearch_memory_updates
            WHERE status IN ({placeholders})
            ORDER BY id DESC
            """,
            tuple(statuses),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM autoresearch_memory_updates ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_due_memory_updates(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    now = _utc_now_iso()
    rows = conn.execute(
        """
        SELECT * FROM autoresearch_memory_updates
        WHERE status IN (?, ?)
          AND apply_after <= ?
        ORDER BY id ASC
        """,
        (*OPEN_MEMORY_UPDATE_STATUSES, now),
    ).fetchall()
    return [dict(r) for r in rows]


def update_memory_update_status(
    conn: sqlite3.Connection,
    update_id: int,
    status: str,
    *,
    error: Optional[str] = None,
    set_applied_at: bool = False,
) -> None:
    now = _utc_now_iso()
    applied_at = now if set_applied_at else None
    conn.execute(
        """
        UPDATE autoresearch_memory_updates
        SET status = ?, error = ?, last_validated_at = ?,
            applied_at = COALESCE(?, applied_at)
        WHERE id = ?
        """,
        (status, error, now, applied_at, update_id),
    )
    conn.commit()


def get_memory_update_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM autoresearch_memory_updates
        GROUP BY status
        """
    ).fetchall()
    out = {
        "proposed": 0,
        "pending_revalidation": 0,
        "applied": 0,
        "discarded": 0,
        "needs_review": 0,
        "failed": 0,
    }
    for r in rows:
        out[r["status"]] = int(r["n"])
    return out
