"""
Golden-answer test for the autoresearch pipeline.

What makes this different from the e2e tests
─────────────────────────────────────────────
The e2e tests in tests/integration/test_autoresearch_e2e.py use a scripted
LLM stub that always returns the correct patch — the "improvement" is written
by the test author, not discovered by the AI.

This test uses a real Claude LLM call (via agent.auxiliary_client) to verify
that the AI independently identifies a documented flaw in a skill file and
proposes a fix that addresses it. The assertion is semantic, not exact-string:
we check that the proposed new_string removes the bad instruction and replaces
it with something safer — without prescribing the exact wording.

Documented scenario
───────────────────
SKILL:  safe-git-push — a synthetic skill that instructs the agent to commit
        and push code changes. It contains a deliberate, documented flaw:

        "3. Push directly to main: `git push origin main`"

        Pushing directly to main is bad practice. Any competent reviewer
        reading this skill should flag it immediately.

FLAW:   Step 3 instructs the agent to push directly to main without checking
        which branch is active. This will silently corrupt main if the user is
        in the middle of feature work.

GOLDEN ANSWER:
        Claude reads the SKILL.md, sees "Push directly to main" alongside a
        high correction rate (5 of 5 sessions had corrections), and proposes a
        patch that removes or replaces the bad instruction with something
        branch-aware.

        We do NOT prescribe what the replacement must say. We only require:
          1. The proposed old_string contains the bad instruction.
          2. The proposed new_string does NOT contain "Push directly to main".

Requirements
────────────
Marked @pytest.mark.integration and @pytest.mark.slow.
Excluded from the default pytest run (addopts = "-m 'not integration'").

To run:
    pytest tests/cron/test_autoresearch_golden_answer.py \\
           --override-ini="addopts=" -v -s
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from cron.autoresearch import run_stage1, run_stage2, run_stage3


# ── Documented flaw ───────────────────────────────────────────────────────────

SKILL_NAME = "safe-git-push"

# The bad instruction — this exact phrase is the documented flaw.
BAD_INSTRUCTION = "Push directly to main: `git push origin main`"

SKILL_CONTENT = """\
# safe-git-push

## When to Use
Use this skill when the user asks to commit and push code changes to the repository.

## Steps

1. Stage all changes: `git add -A`
2. Commit with a clear message: `git commit -m "<descriptive message>"`
3. Push directly to main: `git push origin main`

## Notes
- Use present tense in commit messages: "Add feature" not "Added feature"
- Keep subject lines under 72 characters
- Reference issue numbers where applicable: "Fix #42: ..."
"""

# The word "branch" does not appear in SKILL_CONTENT.
# If it appears in the patched content, the fix is branch-aware.
assert "branch" not in SKILL_CONTENT.lower(), \
    "Test invariant broken: 'branch' must not appear in original skill content"


# ── Correction messages that match _CORRECTION_PATTERNS ───────────────────────
# These are user messages that Stage 1's signal extractor counts as corrections.
# They also hint at the nature of the problem, but Stage 2 does NOT receive the
# raw message text — only the correction count. Claude must identify the flaw
# from the skill content alone.

_CORRECTIONS = [
    "that's wrong, you pushed to main directly",
    "try again — never push to main without checking the branch",
    "that's incorrect, you need to be on a feature branch first",
    "start over, you should not push to main like that",
    "that didn't work, always verify the branch before pushing",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _seed_sessions(conn: sqlite3.Connection) -> None:
    """Seed 5 sessions, all invoking safe-git-push, all with corrections."""
    for i, correction_msg in enumerate(_CORRECTIONS):
        sid = f"s-{i}"
        conn.execute(
            "INSERT INTO sessions "
            "(id, source, input_tokens, output_tokens, tool_call_count, "
            " end_reason, started_at, ended_at, system_prompt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, "cli", 400 + i * 50, 300 + i * 30, 2,
             None, time.time() - (i * 3600), None,
             f"Use the {SKILL_NAME} skill to commit and push the latest changes"),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (sid, "user", correction_msg),
        )
    conn.commit()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def real_llm_call():
    """Provides a real Claude LLM call function.

    Skips the test if:
    - agent.auxiliary_client is not importable
    - The configured LLM client fails a probe call (wrong model, no API key, etc.)

    Behaviour:
      PASS  — Claude available and identifies the flaw correctly
      SKIP  — LLM not configured / model not available in this environment
      FAIL  — Claude is available but does not identify the documented flaw
    """
    try:
        from agent.auxiliary_client import call_llm as _call_llm  # type: ignore
    except ImportError:
        pytest.skip(
            "agent.auxiliary_client not available — "
            "real LLM required for golden-answer test"
        )

    def llm_call(messages):
        resp = _call_llm(messages=messages)
        return resp.choices[0].message.content

    # Probe: send a minimal message to verify the client is working.
    # If the model is not supported or auth fails, skip rather than fail.
    try:
        llm_call([{"role": "user", "content": "Reply with the single word: ok"}])
    except Exception as e:
        pytest.skip(
            f"LLM client not usable in this environment — {type(e).__name__}: {e}\n"
            "Configure a working model/provider to run this test."
        )

    return llm_call


# ── Test ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.slow
class TestGoldenAnswer:
    """
    Verify that real Claude identifies and proposes a fix for a documented flaw.

    This test is the only one in this suite that makes live LLM API calls.
    It is excluded from the default pytest run and should be run explicitly
    when validating the autoresearch loop's AI judgment, not its data pipeline.
    """

    def test_claude_identifies_push_to_main_flaw(self, tmp_path, real_llm_call):
        """
        DOCUMENTED FLAW: safe-git-push tells the agent to push directly to main.

        EXPECTED: Claude reads the skill, sees the high correction rate (5/5),
                  and proposes a patch whose old_string contains the bad
                  instruction and whose new_string removes it.

        GOLDEN ASSERTION:
          - patch["old_string"] contains "Push directly to main"
          - patch["new_string"] does NOT contain "Push directly to main"

        This fails if Claude stops reading the skill content carefully, or if
        the self-play evaluation pipeline breaks when given a real LLM.
        """
        state_db    = tmp_path / "state.db"
        metrics_db  = tmp_path / "skill_metrics.db"
        patches_path = tmp_path / "pending_patches.json"
        digest_path  = tmp_path / "nightly_digest.md"
        report_path  = tmp_path / "nightly_report.md"

        # ── Setup ────────────────────────────────────────────────────────────
        # Create state.db with 5 sessions, all corrected
        conn = _create_state_db(state_db)
        _seed_sessions(conn)
        conn.close()

        # Create SKILL.md containing the documented flaw
        skill_dir = tmp_path / "skills" / SKILL_NAME
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(SKILL_CONTENT, encoding="utf-8")

        # ── Stage 1 ──────────────────────────────────────────────────────────
        run_stage1(
            state_db_path=state_db,
            metrics_db_path=metrics_db,
            report_path=report_path,
            hermes_home=tmp_path,
        )

        assert metrics_db.exists(), "Stage 1 did not create skill_metrics.db"

        db = sqlite3.connect(str(metrics_db))
        row = db.execute(
            "SELECT correction_rate FROM skill_health WHERE skill_name = ?",
            (SKILL_NAME,)
        ).fetchone()
        db.close()

        assert row is not None, f"Stage 1 did not record metrics for '{SKILL_NAME}'"
        correction_rate = row[0]
        assert correction_rate >= 0.9, (
            f"Expected correction_rate ≥ 0.90 (all 5 sessions had corrections), "
            f"got {correction_rate:.2f}"
        )

        report_text = report_path.read_text(encoding="utf-8")
        assert SKILL_NAME in report_text, (
            f"'{SKILL_NAME}' not flagged in Stage 1 report despite 100% correction rate"
        )

        # ── Stage 2 (real Claude) ─────────────────────────────────────────────
        pairs = run_stage2(
            metrics_db_path=metrics_db,
            patches_path=patches_path,
            hermes_home=tmp_path,
            llm_call=real_llm_call,
        )

        assert patches_path.exists(), "Stage 2 did not write pending_patches.json"
        assert len(pairs) >= 1, (
            "Stage 2 produced no patch pairs — Claude either said no patch was "
            "possible or the anomaly was not detected. Check Stage 1 output."
        )

        patch_entry = pairs[0]
        candidate   = patch_entry["candidate"]
        eval_result = patch_entry["eval_result"]

        # ── Golden assertions: Claude identified the flaw ─────────────────────

        old_string = candidate["old_string"]
        new_string = candidate["new_string"]

        assert BAD_INSTRUCTION in old_string, (
            f"GOLDEN ANSWER FAILED:\n"
            f"Claude did not identify the documented flaw.\n"
            f"Expected old_string to contain: {BAD_INSTRUCTION!r}\n"
            f"Actual old_string:              {old_string!r}\n\n"
            f"Full LLM output:\n{candidate.get('raw_llm_output', '(none)')}"
        )

        assert BAD_INSTRUCTION not in new_string, (
            f"GOLDEN ANSWER FAILED:\n"
            f"Claude's proposed fix still contains the bad instruction.\n"
            f"new_string: {new_string!r}"
        )

        # ── Informational: did self-play accept the patch? ────────────────────
        # This is NOT a hard requirement — self-play acceptance depends on token
        # and quality deltas which may vary. The golden answer above (correct
        # old/new_string) is the real test of Claude's judgment.
        status = eval_result.get("status", "unknown")
        if status == "accepted":
            # ── Stage 3 (run only if accepted) ───────────────────────────────
            run_stage3(
                patches_path=patches_path,
                digest_path=digest_path,
                hermes_home=tmp_path,
                dry_run=False,
                run_regression_watch=False,
            )

            patched_content = skill_md.read_text(encoding="utf-8")

            assert BAD_INSTRUCTION not in patched_content, (
                "Stage 3 applied the patch but bad instruction still present in SKILL.md"
            )
            # The fix should introduce branch-awareness (word 'branch' now present)
            assert "branch" in patched_content.lower(), (
                "Stage 3 applied the patch, but the replacement is not branch-aware. "
                f"Patched content:\n{patched_content}"
            )
        else:
            # Self-play did not accept — log why, but don't fail the test.
            # The golden answer (Claude identified the correct old_string) still holds.
            print(
                f"\n[golden-answer] patch status={status!r} — self-play did not accept.\n"
                f"  token_delta={eval_result.get('token_delta'):.3f}  "
                f"quality_delta={eval_result.get('quality_delta'):.3f}\n"
                f"  rejection_reason: {eval_result.get('rejection_reason')}\n"
                f"  hold_reason:      {eval_result.get('hold_reason')}\n"
                f"  This is informational — the golden assertion already passed."
            )
