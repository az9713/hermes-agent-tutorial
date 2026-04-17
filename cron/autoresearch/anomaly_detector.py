"""
anomaly_detector.py -- Detect autoresearch anomalies from skill_health.

Anomaly types:
  - UNDERPERFORMING
  - STRUCTURALLY_BROKEN
  - MISSING_COVERAGE
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

Anomaly = Dict[str, Any]

# Existing thresholds
CORRECTION_RATE_THRESHOLD = 0.30
COMPLETION_RATE_THRESHOLD = 0.50
MIN_INVOCATIONS = 3

# New deterministic anomaly thresholds
STRUCT_BROKEN_CORRECTION_MIN = 0.45
STRUCT_BROKEN_COMPLETION_MAX = 0.35
STRUCT_BROKEN_TOKENS_MIN = 1400.0
STRUCT_BROKEN_TOOL_CALLS_MIN = 5.0

MISSING_COVERAGE_CORRECTION_MIN = 0.30
MISSING_COVERAGE_CORRECTION_MAX = 0.55
MISSING_COVERAGE_COMPLETION_MIN = 0.70


def _aggregate_skill_rows(conn: sqlite3.Connection, days: int) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            skill_name,
            SUM(invocation_count)                                   AS total_invocations,
            AVG(avg_tokens)                                         AS avg_tokens,
            AVG(avg_tool_calls)                                     AS avg_tool_calls,
            AVG(avg_skill_causal_confidence)                        AS avg_skill_causal_confidence,
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


def _build_base(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "skill_name": row["skill_name"],
        "correction_rate": float(row["correction_rate"] or 0.0),
        "completion_rate": float(row["completion_rate"] or 0.0),
        "avg_tokens": float(row["avg_tokens"] or 0.0),
        "avg_tool_calls": float(row["avg_tool_calls"] or 0.0),
        "avg_skill_causal_confidence": float(row["avg_skill_causal_confidence"] or 0.0),
        "invocation_count": int(row["total_invocations"] or 0),
    }


def detect_anomalies(
    conn: sqlite3.Connection,
    days: int = 7,
    min_invocations: int = MIN_INVOCATIONS,
) -> List[Anomaly]:
    """
    Return anomalies from recent skill health aggregates.

    Classification order (mutually exclusive):
      1. STRUCTURALLY_BROKEN
      2. MISSING_COVERAGE
      3. UNDERPERFORMING
    """
    rows = _aggregate_skill_rows(conn, days=days)
    anomalies: List[Anomaly] = []

    for row in rows:
        base = _build_base(row)
        invocations = base["invocation_count"]
        if invocations < min_invocations:
            continue

        correction_rate = base["correction_rate"]
        completion_rate = base["completion_rate"]
        avg_tokens = base["avg_tokens"]
        avg_tool_calls = base["avg_tool_calls"]

        is_structurally_broken = (
            correction_rate >= STRUCT_BROKEN_CORRECTION_MIN
            and completion_rate <= STRUCT_BROKEN_COMPLETION_MAX
            and (avg_tokens >= STRUCT_BROKEN_TOKENS_MIN or avg_tool_calls >= STRUCT_BROKEN_TOOL_CALLS_MIN)
        )
        if is_structurally_broken:
            base.update(
                {
                    "anomaly_type": "STRUCTURALLY_BROKEN",
                    "trigger_metric": (
                        f"correction_rate={correction_rate:.2f}, "
                        f"completion_rate={completion_rate:.2f}, "
                        f"avg_tokens={avg_tokens:.0f}, avg_tool_calls={avg_tool_calls:.1f}"
                    ),
                }
            )
            anomalies.append(base)
            continue

        is_missing_coverage = (
            correction_rate >= MISSING_COVERAGE_CORRECTION_MIN
            and correction_rate < MISSING_COVERAGE_CORRECTION_MAX
            and completion_rate >= MISSING_COVERAGE_COMPLETION_MIN
        )
        if is_missing_coverage:
            base.update(
                {
                    "anomaly_type": "MISSING_COVERAGE",
                    "trigger_metric": (
                        f"correction_rate={correction_rate:.2f}, "
                        f"completion_rate={completion_rate:.2f}"
                    ),
                }
            )
            anomalies.append(base)
            continue

        is_underperforming = (
            correction_rate > CORRECTION_RATE_THRESHOLD
            or completion_rate < COMPLETION_RATE_THRESHOLD
        )
        if is_underperforming:
            trigger_parts = []
            if correction_rate > CORRECTION_RATE_THRESHOLD:
                trigger_parts.append(f"correction_rate={correction_rate:.2f}")
            if completion_rate < COMPLETION_RATE_THRESHOLD:
                trigger_parts.append(f"completion_rate={completion_rate:.2f}")
            base.update(
                {
                    "anomaly_type": "UNDERPERFORMING",
                    "trigger_metric": ", ".join(trigger_parts),
                }
            )
            anomalies.append(base)

    return anomalies
