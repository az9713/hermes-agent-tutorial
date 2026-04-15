"""
Integration tests for cron/autoresearch/__init__.py — the Stage 1 entry point.

What we're testing
──────────────────
run_stage1() wires all Stage 1 components together end-to-end. These tests verify:

1. When state.db is absent, run_stage1() returns a valid report (empty data, no crash).
2. When state.db has sessions, the report reflects those sessions.
3. Signals are stored in skill_metrics.db after running.
4. Running run_stage1() twice doesn't double-count sessions (idempotent ingestion).
5. The report is written to the report_path argument.
6. Flagged skills appear in the report when thresholds are exceeded.

Why these tests matter
──────────────────────
The integration test is the safety net for the entire Stage 1 pipeline. If any piece
(extraction → storage → aggregation → reporting) is broken, these tests fail even if
the unit tests all pass. This is the test that runs in CI to gate Stage 1 deployment.
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from cron.autoresearch import run_stage1
from cron.autoresearch.skill_metrics import open_db


# ── Helpers ───────────────────────────────────────────────────────────────────

def create_state_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
            source        TEXT,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            end_reason    TEXT,
            started_at    REAL,
            ended_at      REAL,
            system_prompt TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT
        );
    """)
    conn.commit()
    return conn


def insert_session(conn, session_id, input_tokens=500, output_tokens=500,
                   system_prompt="", end_reason=None):
    conn.execute(
        """INSERT INTO sessions
           (id, source, input_tokens, output_tokens, tool_call_count,
            end_reason, started_at, ended_at, system_prompt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, "cli", input_tokens, output_tokens, 0,
         end_reason, time.time(), None, system_prompt),
    )
    conn.commit()


def run_stage1_in_tmp(tmp_path: Path, **kwargs):
    """Convenience wrapper that routes all paths into tmp_path."""
    state_db = kwargs.pop("state_db_path", tmp_path / "state.db")
    metrics_db = kwargs.pop("metrics_db_path", tmp_path / "skill_metrics.db")
    report = kwargs.pop("report_path", tmp_path / "nightly_report.md")
    return run_stage1(
        state_db_path=state_db,
        metrics_db_path=metrics_db,
        report_path=report,
        hermes_home=tmp_path,
        **kwargs,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStage1Integration:
    def test_no_state_db_returns_valid_report(self, tmp_path):
        """Stage 1 must not crash when state.db doesn't exist."""
        text = run_stage1_in_tmp(tmp_path)
        assert "Hermes Autoresearch" in text
        assert "Stage 1" in text

    def test_report_written_to_disk(self, tmp_path):
        report_path = tmp_path / "nightly_report.md"
        run_stage1(
            state_db_path=tmp_path / "nonexistent.db",
            metrics_db_path=tmp_path / "metrics.db",
            report_path=report_path,
            hermes_home=tmp_path,
        )
        assert report_path.exists()

    def test_sessions_stored_in_metrics_db(self, tmp_path):
        state_db = tmp_path / "state.db"
        metrics_db = tmp_path / "metrics.db"

        conn = create_state_db(state_db)
        insert_session(conn, "s1")
        insert_session(conn, "s2")
        conn.close()

        run_stage1(
            state_db_path=state_db,
            metrics_db_path=metrics_db,
            report_path=tmp_path / "report.md",
            hermes_home=tmp_path,
            since_hours=1,
        )

        mconn = open_db(metrics_db)
        count = mconn.execute("SELECT COUNT(*) FROM session_signals").fetchone()[0]
        mconn.close()
        assert count == 2

    def test_idempotent_double_run(self, tmp_path):
        """Running Stage 1 twice doesn't double-count sessions in skill_metrics.db."""
        state_db = tmp_path / "state.db"
        metrics_db = tmp_path / "metrics.db"

        conn = create_state_db(state_db)
        insert_session(conn, "s1")
        conn.close()

        for _ in range(2):
            run_stage1(
                state_db_path=state_db,
                metrics_db_path=metrics_db,
                report_path=tmp_path / "report.md",
                hermes_home=tmp_path,
                since_hours=1,
            )

        mconn = open_db(metrics_db)
        count = mconn.execute("SELECT COUNT(*) FROM session_signals").fetchone()[0]
        mconn.close()
        assert count == 1  # not 2

    def test_flagged_skill_in_report(self, tmp_path):
        """A skill with high correction_rate is listed in the Flagged Skills section."""
        # Create a fake skill
        (tmp_path / "skills" / "bad-skill").mkdir(parents=True)
        (tmp_path / "skills" / "bad-skill" / "SKILL.md").write_text("# bad")

        state_db = tmp_path / "state.db"
        conn = create_state_db(state_db)
        for i in range(5):
            insert_session(
                conn, f"s{i}",
                system_prompt="The bad-skill is loaded.",
                end_reason=None,
            )
            # Add correction message for each session
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (f"s{i}", "user", "try again"),
            )
        conn.commit()
        conn.close()

        text = run_stage1_in_tmp(tmp_path, since_hours=1)
        assert "bad-skill" in text
        assert "FLAGGED" in text or "correction_rate" in text

    def test_session_count_in_report(self, tmp_path):
        state_db = tmp_path / "state.db"
        conn = create_state_db(state_db)
        for i in range(4):
            insert_session(conn, f"s{i}")
        conn.close()

        text = run_stage1_in_tmp(tmp_path, since_hours=1)
        assert "4" in text
