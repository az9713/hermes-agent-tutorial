from cron.autoresearch.memory_anomaly_detector import detect_memory_anomalies
from cron.autoresearch.skill_metrics import open_db, record_session_signal


def _seed_signal(conn, snippet: str):
    record_session_signal(
        conn,
        {
            "session_id": "s1",
            "session_date": "2099-01-01",
            "total_tokens": 200,
            "tool_call_count": 1,
            "correction_count": 1,
            "correction_snippets": [snippet],
            "completion_flag": False,
            "skills_invoked": [],
        },
    )


def test_detects_stale_memory_from_contradiction_snippets(tmp_path):
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "Always use branch main for deploy.",
        encoding="utf-8",
    )
    conn = open_db(tmp_path / "metrics.db")
    _seed_signal(conn, "that's wrong, never use branch main for deploy")
    anomalies = detect_memory_anomalies(
        conn, tmp_path, days=7, min_evidence=1, min_evidence_score=0.1
    )
    conn.close()

    assert len(anomalies) == 1
    assert anomalies[0]["target"] == "memory"
    assert anomalies[0]["anomaly_type"] == "STALE_MEMORY"
    assert anomalies[0]["evidence_count"] >= 1


def test_requires_negation_marker(tmp_path):
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "Always use branch main for deploy.",
        encoding="utf-8",
    )
    conn = open_db(tmp_path / "metrics.db")
    _seed_signal(conn, "use branch main for deploy tomorrow")
    anomalies = detect_memory_anomalies(
        conn, tmp_path, days=7, min_evidence=1, min_evidence_score=0.1
    )
    conn.close()
    assert anomalies == []


def test_enforces_min_evidence_threshold(tmp_path):
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "Always use branch main for deploy.",
        encoding="utf-8",
    )
    conn = open_db(tmp_path / "metrics.db")
    _seed_signal(conn, "that's wrong, never use branch main for deploy")
    anomalies = detect_memory_anomalies(
        conn, tmp_path, days=7, min_evidence=2, min_evidence_score=0.1
    )
    conn.close()
    assert anomalies == []


def test_rejects_low_overlap_even_with_negation(tmp_path):
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "Always use branch main for deploy.",
        encoding="utf-8",
    )
    conn = open_db(tmp_path / "metrics.db")
    _seed_signal(conn, "that's wrong, never run database migrations manually")
    anomalies = detect_memory_anomalies(
        conn, tmp_path, days=7, min_evidence=1, min_evidence_score=0.1
    )
    conn.close()
    assert anomalies == []


def test_suppresses_ambiguous_hedged_snippets(tmp_path):
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "Always use branch main for deploy.",
        encoding="utf-8",
    )
    conn = open_db(tmp_path / "metrics.db")
    _seed_signal(conn, "maybe this is wrong, not sure about branch main for deploy")
    anomalies = detect_memory_anomalies(
        conn, tmp_path, days=7, min_evidence=1, min_evidence_score=0.1
    )
    conn.close()
    assert anomalies == []
