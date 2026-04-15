"""
Tests for cron/autoresearch/anomaly_detector.py.

What we're testing
──────────────────
detect_anomalies() reads skill_health rows from skill_metrics.db and returns
UNDERPERFORMING anomalies. These tests verify:

1. Skills within thresholds are not flagged.
2. Skills with correction_rate > 0.30 are flagged as UNDERPERFORMING.
3. Skills with completion_rate < 0.50 are flagged as UNDERPERFORMING.
4. Skills that exceed both thresholds get both trigger_metrics in the string.
5. Skills with fewer than min_invocations are not flagged.
6. Anomalies are sorted by correction_rate descending.
7. An empty skill_health table returns an empty list.
8. The rolling window filter (days param) excludes stale rows.

Why these tests matter
──────────────────────
Anomaly detection is the gate that determines which skills get expensive
LLM hypothesis-generation calls. A false positive wastes money; a false
negative means a broken skill is never fixed. These tests lock in the
threshold logic exactly.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cron.autoresearch.anomaly_detector import (
    COMPLETION_RATE_THRESHOLD,
    CORRECTION_RATE_THRESHOLD,
    MIN_INVOCATIONS,
    detect_anomalies,
)
from cron.autoresearch.skill_metrics import open_db, upsert_skill_health


# ── Helpers ───────────────────────────────────────────────────────────────────

def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_ago(n: int) -> str:
    d = datetime.now(timezone.utc) - timedelta(days=n)
    return d.strftime("%Y-%m-%d")


def insert_health(
    conn,
    skill_name: str,
    correction_rate: float = 0.10,
    completion_rate: float = 0.90,
    invocation_count: int = 5,
    avg_tokens: float = 1000.0,
    health_date: str = None,
):
    upsert_skill_health(
        conn,
        skill_name=skill_name,
        health_date=health_date or today_utc(),
        invocation_count=invocation_count,
        avg_tokens=avg_tokens,
        correction_rate=correction_rate,
        completion_rate=completion_rate,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNoAnomalies:
    def test_empty_db_returns_empty_list(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        result = detect_anomalies(conn)
        conn.close()
        assert result == []

    def test_skill_within_thresholds_not_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "good-skill",
                      correction_rate=0.10, completion_rate=0.90)
        result = detect_anomalies(conn)
        conn.close()
        assert result == []

    def test_skill_at_exact_threshold_not_flagged(self, tmp_path):
        """At exactly the threshold (correction_rate=0.30, completion_rate=0.50), not flagged."""
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "borderline-skill",
                      correction_rate=CORRECTION_RATE_THRESHOLD,
                      completion_rate=COMPLETION_RATE_THRESHOLD)
        result = detect_anomalies(conn)
        conn.close()
        assert result == []


class TestUnderperformingFlag:
    def test_high_correction_rate_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill",
                      correction_rate=CORRECTION_RATE_THRESHOLD + 0.01,
                      completion_rate=0.90)
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["skill_name"] == "bad-skill"
        assert result[0]["anomaly_type"] == "UNDERPERFORMING"

    def test_low_completion_rate_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "incomplete-skill",
                      correction_rate=0.05,
                      completion_rate=COMPLETION_RATE_THRESHOLD - 0.01)
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["skill_name"] == "incomplete-skill"
        assert result[0]["anomaly_type"] == "UNDERPERFORMING"

    def test_both_metrics_exceeded_both_in_trigger(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "very-bad-skill",
                      correction_rate=0.60,
                      completion_rate=0.20)
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        anomaly = result[0]
        assert "correction_rate" in anomaly["trigger_metric"]
        assert "completion_rate" in anomaly["trigger_metric"]

    def test_trigger_metric_contains_rate_value(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill", correction_rate=0.41)
        result = detect_anomalies(conn)
        conn.close()
        assert "0.41" in result[0]["trigger_metric"]

    def test_anomaly_fields_populated(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill",
                      correction_rate=0.40,
                      completion_rate=0.80,
                      invocation_count=7,
                      avg_tokens=1500.0)
        result = detect_anomalies(conn)
        conn.close()
        a = result[0]
        assert a["skill_name"] == "bad-skill"
        assert a["anomaly_type"] == "UNDERPERFORMING"
        assert pytest.approx(a["correction_rate"], abs=1e-6) == 0.40
        assert pytest.approx(a["completion_rate"], abs=1e-6) == 0.80
        assert a["invocation_count"] == 7
        assert a["avg_tokens"] == pytest.approx(1500.0)


class TestMinInvocations:
    def test_below_min_invocations_not_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "rare-skill",
                      correction_rate=0.80,
                      invocation_count=MIN_INVOCATIONS - 1)
        result = detect_anomalies(conn)
        conn.close()
        assert result == []

    def test_exactly_min_invocations_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "rare-skill",
                      correction_rate=0.80,
                      invocation_count=MIN_INVOCATIONS)
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1

    def test_custom_min_invocations(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill",
                      correction_rate=0.80,
                      invocation_count=5)
        result = detect_anomalies(conn, min_invocations=6)
        conn.close()
        assert result == []


class TestSortOrder:
    def test_sorted_by_correction_rate_descending(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "mid-skill", correction_rate=0.40)
        insert_health(conn, "worst-skill", correction_rate=0.70)
        insert_health(conn, "bad-skill", correction_rate=0.50)
        result = detect_anomalies(conn)
        conn.close()
        rates = [a["correction_rate"] for a in result]
        assert rates == sorted(rates, reverse=True)

    def test_ok_skills_excluded_from_sort(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "ok-skill", correction_rate=0.10)
        insert_health(conn, "bad-skill", correction_rate=0.50)
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["skill_name"] == "bad-skill"


class TestRollingWindow:
    def test_stale_rows_excluded(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        # Row from 10 days ago — outside 7-day window
        insert_health(conn, "old-bad-skill",
                      correction_rate=0.80,
                      health_date=days_ago(10))
        result = detect_anomalies(conn, days=7)
        conn.close()
        assert result == []

    def test_recent_rows_included(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill",
                      correction_rate=0.80,
                      health_date=days_ago(3))
        result = detect_anomalies(conn, days=7)
        conn.close()
        assert len(result) == 1

    def test_boundary_day_included(self, tmp_path):
        """The oldest allowed day (exactly `days` days ago) is included."""
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill",
                      correction_rate=0.80,
                      health_date=days_ago(7))
        result = detect_anomalies(conn, days=7)
        conn.close()
        assert len(result) == 1

    def test_custom_days_window(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill",
                      correction_rate=0.80,
                      health_date=days_ago(5))
        # days=3 → row from 5 days ago is outside window
        result = detect_anomalies(conn, days=3)
        conn.close()
        assert result == []


class TestMultipleSkills:
    def test_only_flagged_skills_returned(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "ok-skill", correction_rate=0.05, completion_rate=0.95)
        insert_health(conn, "bad-skill", correction_rate=0.50, completion_rate=0.80)
        insert_health(conn, "incomplete-skill", correction_rate=0.10, completion_rate=0.30)
        result = detect_anomalies(conn)
        conn.close()
        names = {a["skill_name"] for a in result}
        assert "ok-skill" not in names
        assert "bad-skill" in names
        assert "incomplete-skill" in names

    def test_multi_day_weighted_aggregate(self, tmp_path):
        """Rows from different days are weighted by invocation_count before thresholding."""
        conn = open_db(tmp_path / "metrics.db")
        # Day 1: 4 sessions, correction_rate=0.10
        upsert_skill_health(conn, "agg-skill", days_ago(2),
                            invocation_count=4, avg_tokens=1000,
                            correction_rate=0.10, completion_rate=0.90)
        # Day 2: 6 sessions, correction_rate=0.50
        upsert_skill_health(conn, "agg-skill", days_ago(1),
                            invocation_count=6, avg_tokens=1000,
                            correction_rate=0.50, completion_rate=0.90)
        # weighted avg = (0.10*4 + 0.50*6) / 10 = 0.34 → above 0.30 threshold
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["skill_name"] == "agg-skill"
        assert result[0]["correction_rate"] == pytest.approx(0.34)
