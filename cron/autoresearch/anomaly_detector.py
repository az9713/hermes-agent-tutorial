"""
anomaly_detector.py — Reads skill_health from skill_metrics.db and returns
anomalies that warrant a hypothesis-generate cycle.

Stage 2 scope: only UNDERPERFORMING anomalies (bad correction or completion
rates). STRUCTURALLY_BROKEN and MISSING_COVERAGE are deferred to later work.

Returns:
  list[Anomaly]  — each Anomaly is a dict with keys:
    skill_name      str
    anomaly_type    str   ("UNDERPERFORMING")
    trigger_metric  str   human-readable: "correction_rate=0.41"
    correction_rate float
    completion_rate float
    avg_tokens      float
    invocation_count int
"""

from typing import Any, Dict, List, Optional
import sqlite3

# ── Thresholds ────────────────────────────────────────────────────────────────

CORRECTION_RATE_THRESHOLD = 0.30   # flag if > 30% of sessions have corrections
COMPLETION_RATE_THRESHOLD = 0.50   # flag if < 50% of sessions complete naturally
MIN_INVOCATIONS = 3                # skip skills with too few data points


# ── Types ─────────────────────────────────────────────────────────────────────

Anomaly = Dict[str, Any]


# ── Public API ────────────────────────────────────────────────────────────────

def detect_anomalies(
    conn: sqlite3.Connection,
    days: int = 7,
    min_invocations: int = MIN_INVOCATIONS,
) -> List[Anomaly]:
    """Return a list of UNDERPERFORMING anomalies from the last `days` days.

    A skill is UNDERPERFORMING when:
      - correction_rate > CORRECTION_RATE_THRESHOLD, OR
      - completion_rate < COMPLETION_RATE_THRESHOLD
    AND it has been invoked at least `min_invocations` times (statistical floor).

    Args:
        conn:             Open connection to skill_metrics.db (row_factory=sqlite3.Row).
        days:             Rolling window to query (default 7).
        min_invocations:  Minimum invocations required to flag a skill (default 3).

    Returns:
        List of Anomaly dicts, sorted by correction_rate descending (worst first).
    """
    rows = conn.execute(
        """
        SELECT
            skill_name,
            SUM(invocation_count)                                   AS total_invocations,
            AVG(avg_tokens)                                         AS avg_tokens,
            SUM(correction_rate * invocation_count)
                / NULLIF(SUM(invocation_count), 0)                  AS correction_rate,
            SUM(completion_rate * invocation_count)
                / NULLIF(SUM(invocation_count), 0)                  AS completion_rate
        FROM skill_health
        WHERE health_date >= date('now', ?)
        GROUP BY skill_name
        ORDER BY correction_rate DESC
        """,
        (f"-{days} days",),
    ).fetchall()

    anomalies: List[Anomaly] = []
    for row in rows:
        invocations = row["total_invocations"] or 0
        if invocations < min_invocations:
            continue

        correction_rate = row["correction_rate"] or 0.0
        completion_rate = row["completion_rate"] or 0.0

        is_underperforming = (
            correction_rate > CORRECTION_RATE_THRESHOLD
            or completion_rate < COMPLETION_RATE_THRESHOLD
        )
        if not is_underperforming:
            continue

        # Build trigger_metric string: list every metric that exceeded the gate
        trigger_parts = []
        if correction_rate > CORRECTION_RATE_THRESHOLD:
            trigger_parts.append(f"correction_rate={correction_rate:.2f}")
        if completion_rate < COMPLETION_RATE_THRESHOLD:
            trigger_parts.append(f"completion_rate={completion_rate:.2f}")
        trigger_metric = ", ".join(trigger_parts)

        anomalies.append({
            "skill_name": row["skill_name"],
            "anomaly_type": "UNDERPERFORMING",
            "trigger_metric": trigger_metric,
            "correction_rate": correction_rate,
            "completion_rate": completion_rate,
            "avg_tokens": row["avg_tokens"] or 0.0,
            "invocation_count": invocations,
        })

    return anomalies
