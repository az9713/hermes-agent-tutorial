"""
End-to-end integration tests for cron/autoresearch.

What these tests do
───────────────────
These tests call the actual stage code with real SQLite and real file I/O.
Only Stage 2's LLM calls are stubbed (via the injectable llm_call parameter).

Unlike the unit tests (which mock all external dependencies), these tests
prove that data actually flows through the pipeline:

  state.db (seeded) → Stage 1 → skill_metrics.db
  skill_metrics.db  → Stage 2 (stub LLM) → pending_patches.json
  pending_patches.json + SKILL.md → Stage 3 → SKILL.md (modified)

Test classes
────────────
TestStage1RealData   — Stage 1 reads a real state.db, writes a real metrics DB
TestStage3RealSkill  — Stage 3 reads a real patches file, modifies a real SKILL.md
TestFullLoop         — All three stages chain together; underperforming skill gets patched
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from cron.autoresearch import run_stage1, run_stage2, run_stage3
from cron.autoresearch.runner import run_full_loop


# ── Constants ─────────────────────────────────────────────────────────────────

SKILL_NAME = "demo-skill"
OLD_STRING = "Always do the right thing."
NEW_STRING = "Always do the right thing — specifically: confirm branch before push."

SKILL_CONTENT = f"""\
# {SKILL_NAME}

## Rules
- {OLD_STRING}
- Never skip verification.
"""


# ── Setup helpers ─────────────────────────────────────────────────────────────

def _create_state_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id              TEXT PRIMARY KEY,
            source          TEXT,
            input_tokens    INTEGER DEFAULT 0,
            output_tokens   INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            end_reason      TEXT,
            started_at      REAL,
            ended_at        REAL,
            system_prompt   TEXT
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


def _insert_session(conn, session_id, system_prompt="", end_reason=None,
                    input_tokens=500, output_tokens=500):
    conn.execute(
        "INSERT INTO sessions "
        "(id, source, input_tokens, output_tokens, tool_call_count, "
        " end_reason, started_at, ended_at, system_prompt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, "cli", input_tokens, output_tokens, 0,
         end_reason, time.time(), None, system_prompt),
    )
    conn.commit()


def _insert_correction(conn, session_id, msg="try again"):
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, "user", msg),
    )
    conn.commit()


def _make_skill(tmp_path: Path, name: str = SKILL_NAME) -> Path:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_CONTENT, encoding="utf-8")
    return skill_dir


def _write_pending(tmp_path: Path, patches: list) -> Path:
    path = tmp_path / "pending_patches.json"
    path.write_text(json.dumps(patches), encoding="utf-8")
    return path


def _make_patch_entry(skill_name=SKILL_NAME, accepted=True):
    return {
        "skill_name": skill_name,
        "anomaly_type": "UNDERPERFORMING",
        "trigger_metric": "correction_rate=0.60",
        "action": "patch",
        "status": "accepted" if accepted else "rejected",
        "accepted": accepted,
        "token_delta": -0.12,
        "quality_delta": 0.40,
        "judge_scores": [[7.0, 8.0]],
        "old_string": OLD_STRING,
        "new_string": NEW_STRING,
        "reason": "Clarify the rule with a concrete example",
        "hold_reason": "",
        "rejection_reason": "",
        "correction_rate": 0.60,
        "completion_rate": 0.80,
        "avg_tokens": 500.0,
        "generated_at": "2026-04-15T03:00:00Z",
    }


def _make_accepting_llm():
    """Stub LLM for Stage 2 — returns an accepted patch without live API calls.

    Identifies call type by inspecting the last message's content, mirroring
    the approach used in tests/cron/test_autoresearch_stage2.py.
    """
    def _llm(messages):
        last = messages[-1]["content"]

        # Hypothesis call: ask for a targeted patch
        if "Propose a targeted patch" in last or "Identify the specific gap" in last:
            return json.dumps({
                "patch": {
                    "old_string": OLD_STRING,
                    "new_string": NEW_STRING,
                    "reason": "Clarify the rule with a concrete example",
                }
            })

        # Rephrase call: synthetic task generation
        if last.startswith("Rephrase"):
            return "Alternative task description."

        # Agent call: run task with old or new skill version
        if "## Skill" in last:
            if NEW_STRING in last:
                return "Short. " * 5    # new version → fewer tokens
            return "Long response. " * 20  # old version → more tokens

        # Judge call: score old vs new
        if "Score the following" in last:
            if "Short." in last:
                return "8"
            return "6"

        return "5"

    return _llm


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStage1RealData:
    """Stage 1 reads a real state.db and writes a real metrics DB.

    No mocks — the actual signal extractor, metrics writer, and reporter
    run against a real SQLite database seeded with known session data.
    """

    def test_extracts_signals_from_real_db(self, tmp_path):
        """5 sessions, 3 with user corrections → correction_rate ≥ 0.55, skill flagged."""
        state_db = tmp_path / "state.db"
        metrics_db = tmp_path / "skill_metrics.db"
        report_path = tmp_path / "nightly_report.md"

        conn = _create_state_db(state_db)

        # 3 sessions with corrections (triggers anomaly threshold)
        for i in range(3):
            sid = f"s-corr-{i}"
            _insert_session(conn, sid,
                            system_prompt=f"Use the {SKILL_NAME} skill for this task")
            _insert_correction(conn, sid)

        # 2 clean sessions
        for i in range(2):
            _insert_session(conn, f"s-clean-{i}",
                            system_prompt=f"Use the {SKILL_NAME} skill for this task",
                            end_reason="cli_close")
        conn.close()

        # Stage 1 discovers skill names by scanning hermes_home/skills/*/
        _make_skill(tmp_path)

        run_stage1(
            state_db_path=state_db,
            metrics_db_path=metrics_db,
            report_path=report_path,
            hermes_home=tmp_path,
        )

        # Metrics DB must exist with a row for demo-skill
        assert metrics_db.exists(), "Stage 1 did not create skill_metrics.db"
        db = sqlite3.connect(str(metrics_db))
        row = db.execute(
            "SELECT skill_name, correction_rate FROM skill_health WHERE skill_name = ?",
            (SKILL_NAME,)
        ).fetchone()
        db.close()

        assert row is not None, f"'{SKILL_NAME}' not found in skill_health table"
        _, correction_rate = row
        assert correction_rate >= 0.55, (
            f"Expected correction_rate ≥ 0.55 (3 of 5 sessions had corrections), "
            f"got {correction_rate:.2f}"
        )

        # Report must mention the flagged skill
        assert report_path.exists(), "Stage 1 did not write nightly_report.md"
        report_text = report_path.read_text(encoding="utf-8")
        assert SKILL_NAME in report_text, (
            f"Expected '{SKILL_NAME}' to appear in nightly report (flagged skills)"
        )


class TestStage3RealSkill:
    """Stage 3 reads a real pending_patches.json and modifies a real SKILL.md.

    No mocks — the actual patch applier runs against a real SKILL.md file.
    """

    def test_accepted_patch_modifies_skill_md(self, tmp_path):
        """Accepted patch → SKILL.md contains new string; history entry recorded."""
        _make_skill(tmp_path)
        _write_pending(tmp_path, [_make_patch_entry(accepted=True)])

        digest = run_stage3(
            patches_path=tmp_path / "pending_patches.json",
            digest_path=tmp_path / "nightly_digest.md",
            hermes_home=tmp_path,
            dry_run=False,
            run_regression_watch=False,
        )

        skill_md = tmp_path / "skills" / SKILL_NAME / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")

        assert NEW_STRING in content, (
            f"New string not in SKILL.md after patch.\n"
            f"Expected: {NEW_STRING!r}\n"
            f"SKILL.md:\n{content}"
        )
        assert OLD_STRING not in content, (
            "Old string still present in SKILL.md — patch did not replace it"
        )

        history = tmp_path / "skills" / SKILL_NAME / "SKILL_HISTORY.md"
        assert history.exists(), "Stage 3 did not create SKILL_HISTORY.md"
        assert "[autoresearch]" in history.read_text(encoding="utf-8")

        assert SKILL_NAME in digest, "Digest does not mention the patched skill"

    def test_dry_run_leaves_skill_md_unchanged(self, tmp_path):
        """dry_run=True → SKILL.md not touched, original content preserved."""
        _make_skill(tmp_path)
        _write_pending(tmp_path, [_make_patch_entry(accepted=True)])

        run_stage3(
            patches_path=tmp_path / "pending_patches.json",
            digest_path=tmp_path / "nightly_digest.md",
            hermes_home=tmp_path,
            dry_run=True,
            run_regression_watch=False,
        )

        skill_md = tmp_path / "skills" / SKILL_NAME / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert OLD_STRING in content, (
            "dry_run=True should not modify SKILL.md; old string missing"
        )
        assert NEW_STRING not in content, (
            "dry_run=True should not modify SKILL.md; new string appeared"
        )


class TestFullLoop:
    """All three stages chain: state.db → Stage 1 → Stage 2 (stub LLM) → Stage 3.

    This is the end-to-end proof that the autoresearch pipeline can detect an
    underperforming skill and improve it without manual intervention.
    """

    def test_full_pipeline_improves_skill(self, tmp_path):
        """
        Scenario: demo-skill has a 80% correction rate (4 of 5 sessions).
        Expected: Stage 1 flags it; Stage 2 generates an accepted patch;
                  Stage 3 applies the patch; SKILL.md is improved.
        """
        state_db = tmp_path / "state.db"
        metrics_db = tmp_path / "metrics.db"
        patches_path = tmp_path / "pending_patches.json"
        digest_path = tmp_path / "nightly_digest.md"
        state_path = tmp_path / "state.json"

        # Seed state.db: 4 corrected sessions + 1 clean
        conn = _create_state_db(state_db)
        for i in range(4):
            sid = f"s-{i}"
            _insert_session(conn, sid,
                            system_prompt=f"Use the {SKILL_NAME} skill for this task")
            _insert_correction(conn, sid)
        _insert_session(conn, "s-clean",
                        system_prompt=f"Use the {SKILL_NAME} skill for this task",
                        end_reason="cli_close")
        conn.close()

        # Create SKILL.md with the vague rule that will be patched
        _make_skill(tmp_path)

        digest = run_full_loop(
            hermes_home=tmp_path,
            metrics_db_path=metrics_db,
            patches_path=patches_path,
            digest_path=digest_path,
            state_path=state_path,
            dry_run=False,
            run_regression_watch=False,
            skip_stage2=False,
            llm_call=_make_accepting_llm(),
        )

        # ── Stage 1 evidence ─────────────────────────────────────────────────
        assert metrics_db.exists(), "Stage 1 did not create skill_metrics.db"

        # ── Stage 2 evidence ─────────────────────────────────────────────────
        assert patches_path.exists(), "Stage 2 did not create pending_patches.json"
        patches = json.loads(patches_path.read_text(encoding="utf-8"))
        accepted = [p for p in patches if p.get("accepted")]
        assert accepted, (
            "Stage 2 produced no accepted patches — the underperforming skill "
            "should have triggered an accepted hypothesis"
        )

        # ── Stage 3 evidence ─────────────────────────────────────────────────
        skill_md = tmp_path / "skills" / SKILL_NAME / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")

        assert NEW_STRING in content, (
            f"Stage 3 did not apply patch to SKILL.md.\n"
            f"Expected new string: {NEW_STRING!r}\n"
            f"SKILL.md content:\n{content}"
        )
        assert OLD_STRING not in content, (
            "Old vague rule still present — Stage 3 did not replace it"
        )

        # ── State persistence ─────────────────────────────────────────────────
        assert state_path.exists(), "run_full_loop did not write state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["last_status"] == "ok", (
            f"Expected last_status='ok', got {state['last_status']!r}. "
            f"Error: {state.get('last_error')}"
        )
        assert state["last_run_at"] is not None

        # ── Digest ────────────────────────────────────────────────────────────
        assert isinstance(digest, str) and len(digest) > 0
