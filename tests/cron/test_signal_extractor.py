"""
Tests for cron/autoresearch/signal_extractor.py — signal extraction from state.db.

What we're testing
──────────────────
signal_extractor.py reads live Hermes state.db and produces structured signals.
These tests verify:

1. Returns [] when state.db doesn't exist (graceful degradation).
2. Returns [] when no sessions fall within the since_hours window.
3. Correction detection: _CORRECTION_PATTERNS catches explicit correction phrases.
4. Completion detection: natural end_reason OR acknowledgment in last user message.
5. Skill detection: skill names present in system_prompt are included in skills_invoked.
6. Token counting: total_tokens = input_tokens + output_tokens.
7. Session date: derived from started_at timestamp (UTC).
8. Full extract_signals() wiring: multiple sessions produce multiple signals.

Why these tests matter
──────────────────────
Signals are the raw input to every downstream calculation. If correction_count is
wrong, correction_rate is wrong. If skills_invoked is wrong, skill_health is wrong.
These tests pin the exact detection behaviour so regressions are caught immediately.
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from cron.autoresearch.signal_extractor import (
    _check_completion,
    _count_corrections,
    _detect_skills_in_prompt,
    _get_known_skill_names,
    extract_signals,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def create_state_db(path: Path) -> sqlite3.Connection:
    """Create a minimal state.db with sessions and messages tables."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
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


def insert_session(
    conn: sqlite3.Connection,
    session_id: str = "s1",
    input_tokens: int = 500,
    output_tokens: int = 500,
    tool_call_count: int = 3,
    end_reason: str = None,
    started_at: float = None,
    system_prompt: str = "",
) -> None:
    conn.execute(
        """INSERT INTO sessions
           (id, source, input_tokens, output_tokens, tool_call_count,
            end_reason, started_at, ended_at, system_prompt)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, "cli", input_tokens, output_tokens, tool_call_count,
         end_reason, started_at or time.time(), None, system_prompt),
    )
    conn.commit()


def insert_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
) -> None:
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content),
    )
    conn.commit()


def make_row(data: dict) -> sqlite3.Row:
    """Build a fake sqlite3.Row-like object from a dict using an in-memory DB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = list(data.keys())
    vals = list(data.values())
    placeholders = ", ".join("?" * len(vals))
    col_defs = ", ".join(f"{c} TEXT" for c in cols)
    conn.execute(f"CREATE TABLE t ({col_defs})")
    conn.execute(f"INSERT INTO t VALUES ({placeholders})", vals)
    return conn.execute("SELECT * FROM t").fetchone()


# ── Tests: graceful degradation ────────────────────────────────────────────────

class TestGracefulDegradation:
    def test_no_state_db_returns_empty(self, tmp_path):
        result = extract_signals(state_db_path=tmp_path / "nonexistent.db")
        assert result == []

    def test_no_sessions_in_window_returns_empty(self, tmp_path):
        db_path = tmp_path / "state.db"
        conn = create_state_db(db_path)
        # Insert session 1 week ago — outside the default 24h window
        old_ts = time.time() - 7 * 24 * 3600
        insert_session(conn, started_at=old_ts)
        conn.close()
        result = extract_signals(state_db_path=db_path, since_hours=24)
        assert result == []


# ── Tests: _count_corrections ────────────────────────────────────────────────

class TestCountCorrections:
    def _make_messages(self, contents: list) -> list:
        """Build a list of Row-like objects from (role, content) tuples."""
        return [make_row({"role": r, "content": c}) for r, c in contents]

    def test_no_messages_returns_zero(self):
        assert _count_corrections([]) == 0

    def test_no_correction_in_user_messages(self):
        msgs = self._make_messages([("user", "sounds good"), ("assistant", "ok")])
        assert _count_corrections(msgs) == 0

    def test_try_again_detected(self):
        msgs = self._make_messages([("user", "try again please")])
        assert _count_corrections(msgs) == 1

    def test_thats_wrong_detected(self):
        msgs = self._make_messages([("user", "that's wrong, let me rephrase")])
        assert _count_corrections(msgs) == 1

    def test_incorrect_standalone_detected(self):
        msgs = self._make_messages([("user", "that is incorrect")])
        assert _count_corrections(msgs) == 1

    def test_start_over_detected(self):
        msgs = self._make_messages([("user", "start over from the beginning")])
        assert _count_corrections(msgs) == 1

    def test_assistant_messages_ignored(self):
        msgs = self._make_messages([("assistant", "try again if that doesn't work")])
        assert _count_corrections(msgs) == 0

    def test_json_content_skipped(self):
        """Tool result content starting with [ or { is not scanned."""
        msgs = self._make_messages([("user", '["try again"]')])
        assert _count_corrections(msgs) == 0

    def test_multiple_corrections_counted(self):
        msgs = self._make_messages([
            ("user", "try again"),
            ("user", "that's wrong"),
            ("user", "start over"),
        ])
        assert _count_corrections(msgs) == 3


# ── Tests: _check_completion ─────────────────────────────────────────────────

class TestCheckCompletion:
    def test_natural_end_reason_cli_close(self):
        session = make_row({"end_reason": "cli_close"})
        assert _check_completion(session, [])

    def test_natural_end_reason_user_quit(self):
        session = make_row({"end_reason": "user_quit"})
        assert _check_completion(session, [])

    def test_unknown_end_reason_is_not_complete(self):
        session = make_row({"end_reason": "timeout"})
        msgs = [make_row({"role": "user", "content": "hmm ok"})]
        assert not _check_completion(session, msgs)

    def test_last_user_message_thanks_is_complete(self):
        session = make_row({"end_reason": None})
        msgs = [make_row({"role": "user", "content": "thanks!"})]
        assert _check_completion(session, msgs)

    def test_last_user_message_perfect_is_complete(self):
        session = make_row({"end_reason": None})
        msgs = [make_row({"role": "user", "content": "perfect, done"})]
        assert _check_completion(session, msgs)

    def test_last_user_message_looks_good_is_complete(self):
        session = make_row({"end_reason": None})
        msgs = [make_row({"role": "user", "content": "looks good to me"})]
        assert _check_completion(session, msgs)

    def test_non_completion_message_returns_false(self):
        session = make_row({"end_reason": None})
        msgs = [make_row({"role": "user", "content": "what about the edge case?"})]
        assert not _check_completion(session, msgs)

    def test_only_last_user_message_checked(self):
        """Even if an earlier message has a completion word, it's the LAST that counts."""
        session = make_row({"end_reason": None})
        msgs = [
            make_row({"role": "user", "content": "thanks for the first part"}),
            make_row({"role": "assistant", "content": "here is the next part"}),
            make_row({"role": "user", "content": "hmm this is wrong"}),
        ]
        # last user message has no completion acknowledgment
        assert not _check_completion(session, msgs)


# ── Tests: _detect_skills_in_prompt ─────────────────────────────────────────

class TestDetectSkillsInPrompt:
    def test_empty_prompt_returns_empty(self):
        result = _detect_skills_in_prompt("", ["git-workflow"])
        assert result == []

    def test_empty_skill_list_returns_empty(self):
        result = _detect_skills_in_prompt("some system prompt with git-workflow", [])
        assert result == []

    def test_skill_present_is_detected(self):
        result = _detect_skills_in_prompt("use git-workflow skill here", ["git-workflow"])
        assert "git-workflow" in result

    def test_skill_absent_not_detected(self):
        result = _detect_skills_in_prompt("general instructions", ["git-workflow"])
        assert result == []

    def test_multiple_skills_detected(self):
        prompt = "Skills loaded: git-workflow, web-search"
        result = _detect_skills_in_prompt(prompt, ["git-workflow", "web-search", "unknown"])
        assert "git-workflow" in result
        assert "web-search" in result
        assert "unknown" not in result

    def test_case_insensitive_match(self):
        result = _detect_skills_in_prompt("GIT-WORKFLOW is active", ["git-workflow"])
        assert "git-workflow" in result


# ── Tests: _get_known_skill_names ────────────────────────────────────────────

class TestGetKnownSkillNames:
    def test_returns_empty_when_skills_dir_missing(self, tmp_path):
        result = _get_known_skill_names(hermes_home=tmp_path)
        assert result == []

    def test_returns_skill_dir_names(self, tmp_path):
        skills_root = tmp_path / "skills"
        (skills_root / "git-workflow").mkdir(parents=True)
        (skills_root / "git-workflow" / "SKILL.md").write_text("# skill")
        result = _get_known_skill_names(hermes_home=tmp_path)
        assert "git-workflow" in result

    def test_dirs_without_skill_md_excluded(self, tmp_path):
        skills_root = tmp_path / "skills"
        (skills_root / "not-a-skill").mkdir(parents=True)
        # No SKILL.md inside
        result = _get_known_skill_names(hermes_home=tmp_path)
        assert "not-a-skill" not in result


# ── Tests: extract_signals (integration) ─────────────────────────────────────

class TestExtractSignals:
    def test_returns_one_signal_per_session(self, tmp_path):
        db_path = tmp_path / "state.db"
        conn = create_state_db(db_path)
        for i in range(3):
            insert_session(conn, session_id=f"s{i}", started_at=time.time())
        conn.close()
        result = extract_signals(state_db_path=db_path, since_hours=1)
        assert len(result) == 3

    def test_signal_has_required_keys(self, tmp_path):
        db_path = tmp_path / "state.db"
        conn = create_state_db(db_path)
        insert_session(conn, session_id="s1", started_at=time.time())
        conn.close()
        result = extract_signals(state_db_path=db_path, since_hours=1)
        signal = result[0]
        for key in ("session_id", "session_date", "total_tokens", "tool_call_count",
                    "correction_count", "completion_flag", "skills_invoked"):
            assert key in signal

    def test_total_tokens_sum(self, tmp_path):
        db_path = tmp_path / "state.db"
        conn = create_state_db(db_path)
        insert_session(conn, "s1", input_tokens=300, output_tokens=700, started_at=time.time())
        conn.close()
        result = extract_signals(state_db_path=db_path, since_hours=1)
        assert result[0]["total_tokens"] == 1000

    def test_correction_count_from_messages(self, tmp_path):
        db_path = tmp_path / "state.db"
        conn = create_state_db(db_path)
        insert_session(conn, "s1", started_at=time.time())
        insert_message(conn, "s1", "user", "try again")
        insert_message(conn, "s1", "user", "start over")
        conn.close()
        result = extract_signals(state_db_path=db_path, since_hours=1)
        assert result[0]["correction_count"] == 2

    def test_skills_invoked_from_system_prompt(self, tmp_path):
        # Create a fake skill in the hermes_home
        (tmp_path / "skills" / "git-workflow").mkdir(parents=True)
        (tmp_path / "skills" / "git-workflow" / "SKILL.md").write_text("# git")

        db_path = tmp_path / "state.db"
        conn = create_state_db(db_path)
        insert_session(
            conn, "s1", started_at=time.time(),
            system_prompt="Use the git-workflow skill for commits.",
        )
        conn.close()
        result = extract_signals(
            state_db_path=db_path, since_hours=1, hermes_home=tmp_path
        )
        assert "git-workflow" in result[0]["skills_invoked"]

    def test_session_date_is_utc_yyyy_mm_dd(self, tmp_path):
        db_path = tmp_path / "state.db"
        conn = create_state_db(db_path)
        insert_session(conn, "s1", started_at=time.time())
        conn.close()
        result = extract_signals(state_db_path=db_path, since_hours=1)
        import re
        assert re.match(r"\d{4}-\d{2}-\d{2}", result[0]["session_date"])
