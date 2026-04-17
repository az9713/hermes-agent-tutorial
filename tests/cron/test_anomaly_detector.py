from datetime import datetime, timedelta, timezone

import pytest

from cron.autoresearch.anomaly_detector import (
    COMPLETION_RATE_THRESHOLD,
    CORRECTION_RATE_THRESHOLD,
    MIN_INVOCATIONS,
    detect_anomalies,
)
from cron.autoresearch.skill_metrics import open_db, upsert_skill_health


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
    avg_tool_calls: float = 2.0,
    health_date: str | None = None,
):
    upsert_skill_health(
        conn,
        skill_name=skill_name,
        health_date=health_date or today_utc(),
        invocation_count=invocation_count,
        avg_tokens=avg_tokens,
        avg_tool_calls=avg_tool_calls,
        correction_rate=correction_rate,
        completion_rate=completion_rate,
    )


class TestNoAnomalies:
    def test_empty_db_returns_empty_list(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        result = detect_anomalies(conn)
        conn.close()
        assert result == []

    def test_skill_within_thresholds_not_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "good-skill", correction_rate=0.10, completion_rate=0.90)
        result = detect_anomalies(conn)
        conn.close()
        assert result == []

    def test_skill_at_exact_threshold_not_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(
            conn,
            "borderline-skill",
            correction_rate=CORRECTION_RATE_THRESHOLD,
            completion_rate=COMPLETION_RATE_THRESHOLD,
        )
        result = detect_anomalies(conn)
        conn.close()
        assert result == []


class TestUnderperforming:
    def test_high_correction_rate_flagged_as_underperforming(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill", correction_rate=0.60, completion_rate=0.80)
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["anomaly_type"] == "UNDERPERFORMING"

    def test_low_completion_rate_flagged_as_underperforming(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "incomplete-skill", correction_rate=0.20, completion_rate=0.45)
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["anomaly_type"] == "UNDERPERFORMING"


class TestStructurallyBroken:
    def test_structurally_broken_detected(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(
            conn,
            "broken-skill",
            correction_rate=0.55,
            completion_rate=0.30,
            avg_tokens=1900.0,
            avg_tool_calls=7.0,
        )
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["anomaly_type"] == "STRUCTURALLY_BROKEN"
        assert "avg_tool_calls" in result[0]["trigger_metric"]


class TestMissingCoverage:
    def test_missing_coverage_detected(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(
            conn,
            "missing-coverage",
            correction_rate=0.40,
            completion_rate=0.85,
            avg_tokens=900.0,
            avg_tool_calls=1.5,
        )
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["anomaly_type"] == "MISSING_COVERAGE"


class TestMinInvocations:
    def test_below_min_invocations_not_flagged(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "rare-skill", correction_rate=0.80, invocation_count=MIN_INVOCATIONS - 1)
        result = detect_anomalies(conn)
        conn.close()
        assert result == []


class TestSortOrder:
    def test_sorted_by_correction_rate_descending(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "mid-skill", correction_rate=0.60)
        insert_health(conn, "worst-skill", correction_rate=0.70)
        insert_health(conn, "bad-skill", correction_rate=0.65)
        result = detect_anomalies(conn)
        conn.close()
        rates = [a["correction_rate"] for a in result]
        assert rates == sorted(rates, reverse=True)


class TestRollingWindow:
    def test_stale_rows_excluded(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "old-bad-skill", correction_rate=0.80, health_date=days_ago(10))
        result = detect_anomalies(conn, days=7)
        conn.close()
        assert result == []

    def test_recent_rows_included(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "bad-skill", correction_rate=0.80, health_date=days_ago(3))
        result = detect_anomalies(conn, days=7)
        conn.close()
        assert len(result) == 1


class TestMultipleSkills:
    def test_only_flagged_skills_returned(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        insert_health(conn, "ok-skill", correction_rate=0.05, completion_rate=0.95)
        insert_health(conn, "bad-skill", correction_rate=0.60, completion_rate=0.80)
        insert_health(conn, "incomplete-skill", correction_rate=0.10, completion_rate=0.30)
        result = detect_anomalies(conn)
        conn.close()
        names = {a["skill_name"] for a in result}
        assert "ok-skill" not in names
        assert "bad-skill" in names
        assert "incomplete-skill" in names

    def test_multi_day_weighted_aggregate(self, tmp_path):
        conn = open_db(tmp_path / "metrics.db")
        upsert_skill_health(
            conn,
            "agg-skill",
            days_ago(2),
            invocation_count=4,
            avg_tokens=1000,
            correction_rate=0.20,
            completion_rate=0.90,
        )
        upsert_skill_health(
            conn,
            "agg-skill",
            days_ago(1),
            invocation_count=6,
            avg_tokens=1000,
            correction_rate=0.80,
            completion_rate=0.90,
        )
        result = detect_anomalies(conn)
        conn.close()
        assert len(result) == 1
        assert result[0]["skill_name"] == "agg-skill"
        assert result[0]["correction_rate"] == pytest.approx(0.56)
