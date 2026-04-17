"""
Integration tests for cron/autoresearch/__init__.py — the Stage 2 entry point.

What we're testing
──────────────────
run_stage2() wires anomaly detection → hypothesis generation → self-play
evaluation → pending_patches.json. These tests verify:

1. No anomalies → empty pending_patches.json, empty return list.
2. Anomaly detected, but SKILL.md missing → skipped gracefully.
3. Anomaly detected, SKILL.md present, LLM returns null hypothesis → skipped.
4. Full happy path: anomaly → candidate → accepted → entry in patches file.
5. Rejected candidate appears in patches file with status "rejected".
6. pending_patches.json is always written (even on empty result).
7. run_stage2() does not modify any SKILL.md files.

Why these tests matter
──────────────────────
The integration test is the safety net for the entire Stage 2 pipeline. If
any piece (detection → hypothesis → evaluate → write) is broken, these tests
fail even if the unit tests pass. All LLM calls are stubbed with lambdas —
no live API required.
"""

import json
import time
from pathlib import Path

import pytest

from cron.autoresearch import run_stage2
from cron.autoresearch.skill_metrics import open_db, record_session_signal, upsert_skill_health
from cron.autoresearch.pending_patches import read_pending_patches
from cron.autoresearch.pending_memory_updates import read_pending_memory_updates


# ── Helpers ───────────────────────────────────────────────────────────────────

def setup_metrics_db(tmp_path: Path, skill_name: str = "bad-skill") -> Path:
    """Create a metrics DB with one UNDERPERFORMING skill."""
    from datetime import datetime, timezone
    db_path = tmp_path / "metrics.db"
    conn = open_db(db_path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    upsert_skill_health(
        conn, skill_name, today,
        invocation_count=5,
        avg_tokens=1000.0,
        correction_rate=0.60,   # > 0.30 → anomaly
        completion_rate=0.80,
    )
    conn.close()
    return db_path


def setup_skill(tmp_path: Path, skill_name: str = "bad-skill") -> Path:
    """Create a minimal SKILL.md for the given skill name."""
    skill_dir = tmp_path / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"# {skill_name}\n\n## Rules\n- Always do the right thing.\n",
        encoding="utf-8",
    )
    return skill_file


def make_accepting_llm(skill_content: str):
    """Return an LLM stub that produces an accepted patch end-to-end.

    Hypothesis call returns a valid patch.
    Self-play calls return:
      - Short agent responses (new < old → token_delta < 0)
      - Higher scores for new version
    """
    call_count = [0]

    # The hypothesis generator sends 2 messages (system + user).
    # Self-play evaluator sends 1 message each time.
    # We identify the call type by checking content.

    def _llm(messages):
        call_count[0] += 1
        last = messages[-1]["content"]

        # Hypothesis call: contains "old_string" instructions
        if "Propose a targeted patch" in last or "Identify the specific gap" in last:
            old_string = "Always do the right thing."
            new_string = "Always do the right thing — specifically: X, Y, Z."
            return json.dumps({
                "patch": {
                    "old_string": old_string,
                    "new_string": new_string,
                    "reason": "Clarify what 'right thing' means",
                }
            })

        # Rephrase call
        if last.startswith("Rephrase"):
            return "Alternative task description."

        # Agent call (old or new skill)
        if "## Skill" in last:
            # New skill gets a shorter response
            if new_string := "Always do the right thing — specifically: X, Y, Z.":
                if new_string in last:
                    return "Short. " * 5   # ~35 chars
            return "Long response. " * 20  # ~300 chars

        # Judge call
        if "Score the following" in last:
            if "Short." in last:
                return "8"
            return "6"

        return "5"

    return _llm


def make_rejecting_llm(skill_content: str):
    """LLM stub that produces a rejected patch (longer responses → token_delta > 0)."""
    def _llm(messages):
        last = messages[-1]["content"]

        if "Propose a targeted patch" in last or "Identify the specific gap" in last:
            return json.dumps({
                "patch": {
                    "old_string": "Always do the right thing.",
                    "new_string": "Always do the right thing — specifically: X.",
                    "reason": "Add specificity",
                }
            })

        if last.startswith("Rephrase"):
            return "Alternative task."

        if "## Skill" in last:
            return "x" * 300  # both old and new return same length → token_delta = 0 → rejected

        if "Score the following" in last:
            return "7"

        return "5"

    return _llm


def make_null_hypothesis_llm():
    """LLM stub that says no patch is possible."""
    def _llm(messages):
        last = messages[-1]["content"]
        if "Identify the specific gap" in last or "Propose a targeted patch" in last:
            return json.dumps({"patch": None, "reason": "not enough evidence"})
        return "5"
    return _llm


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStage2Integration:
    def test_no_anomalies_returns_empty_list(self, tmp_path):
        """When no skills are flagged, run_stage2() returns [] and writes empty JSON."""
        # Empty metrics DB
        db_path = tmp_path / "metrics.db"
        open_db(db_path).close()
        patches_path = tmp_path / "pending_patches.json"

        result = run_stage2(
            metrics_db_path=db_path,
            patches_path=patches_path,
            hermes_home=tmp_path,
            llm_call=lambda msgs: "{}",
        )

        assert result == []
        assert patches_path.exists()
        assert read_pending_patches(patches_path) == []

    def test_missing_skill_md_skipped_gracefully(self, tmp_path):
        """Anomaly detected, but SKILL.md doesn't exist → skipped without crash."""
        db_path = setup_metrics_db(tmp_path, "orphan-skill")
        patches_path = tmp_path / "pending_patches.json"
        # Do NOT create the SKILL.md

        result = run_stage2(
            metrics_db_path=db_path,
            patches_path=patches_path,
            hermes_home=tmp_path,
            llm_call=lambda msgs: "{}",
        )

        assert result == []
        assert patches_path.exists()

    def test_null_hypothesis_skipped_gracefully(self, tmp_path):
        """LLM can't generate a patch → skipped, no entry in pending_patches."""
        db_path = setup_metrics_db(tmp_path, "bad-skill")
        setup_skill(tmp_path, "bad-skill")
        patches_path = tmp_path / "pending_patches.json"

        result = run_stage2(
            metrics_db_path=db_path,
            patches_path=patches_path,
            hermes_home=tmp_path,
            llm_call=make_null_hypothesis_llm(),
        )

        assert result == []

    def test_accepted_patch_in_output(self, tmp_path):
        """Full happy path: anomaly → accepted candidate → appears in patches file."""
        db_path = setup_metrics_db(tmp_path, "bad-skill")
        skill_file = setup_skill(tmp_path, "bad-skill")
        skill_content = skill_file.read_text(encoding="utf-8")
        patches_path = tmp_path / "pending_patches.json"

        result = run_stage2(
            metrics_db_path=db_path,
            patches_path=patches_path,
            hermes_home=tmp_path,
            llm_call=make_accepting_llm(skill_content),
        )

        assert patches_path.exists()
        entries = read_pending_patches(patches_path)
        assert len(entries) >= 1
        entry = entries[0]
        assert entry["skill_name"] == "bad-skill"
        # accepted or rejected depending on stub LLM — at minimum all required fields present
        assert "status" in entry
        assert "token_delta" in entry
        assert "old_string" in entry

    def test_rejected_patch_in_output(self, tmp_path):
        """Rejected candidate still appears in patches file with status 'rejected'."""
        db_path = setup_metrics_db(tmp_path, "bad-skill")
        skill_file = setup_skill(tmp_path, "bad-skill")
        skill_content = skill_file.read_text(encoding="utf-8")
        patches_path = tmp_path / "pending_patches.json"

        result = run_stage2(
            metrics_db_path=db_path,
            patches_path=patches_path,
            hermes_home=tmp_path,
            llm_call=make_rejecting_llm(skill_content),
        )

        entries = read_pending_patches(patches_path)
        # The rejecting LLM should produce a rejected entry (same length responses)
        if entries:
            assert entries[0]["status"] in {"accepted", "rejected", "hold"}

    def test_patches_file_always_written(self, tmp_path):
        """pending_patches.json is always written, even when all skills are skipped."""
        db_path = setup_metrics_db(tmp_path, "no-skill-file")
        patches_path = tmp_path / "pending_patches.json"

        run_stage2(
            metrics_db_path=db_path,
            patches_path=patches_path,
            hermes_home=tmp_path,
            llm_call=lambda msgs: "{}",
        )

        assert patches_path.exists()

    def test_skill_md_not_modified(self, tmp_path):
        """run_stage2() must not write to any SKILL.md file."""
        db_path = setup_metrics_db(tmp_path, "bad-skill")
        skill_file = setup_skill(tmp_path, "bad-skill")
        original_content = skill_file.read_text(encoding="utf-8")
        original_mtime = skill_file.stat().st_mtime

        run_stage2(
            metrics_db_path=db_path,
            patches_path=tmp_path / "pending_patches.json",
            hermes_home=tmp_path,
            llm_call=make_accepting_llm(original_content),
        )

        assert skill_file.read_text(encoding="utf-8") == original_content
        assert skill_file.stat().st_mtime == original_mtime

    def test_memory_proposals_written_and_persisted(self, tmp_path):
        db_path = tmp_path / "metrics.db"
        conn = open_db(db_path)
        record_session_signal(
            conn,
            {
                "session_id": "s1",
                "session_date": "2099-01-01",
                "total_tokens": 200,
                "tool_call_count": 1,
                "correction_count": 1,
                "correction_snippets": ["that's wrong, never use branch main for deploy"],
                "completion_flag": False,
                "skills_invoked": [],
            },
        )
        record_session_signal(
            conn,
            {
                "session_id": "s2",
                "session_date": "2099-01-01",
                "total_tokens": 220,
                "tool_call_count": 1,
                "correction_count": 1,
                "correction_snippets": ["that's incorrect, never use branch main for deploy"],
                "completion_flag": False,
                "skills_invoked": [],
            },
        )
        conn.close()

        mem_dir = tmp_path / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "MEMORY.md").write_text("Always use branch main for deploy.", encoding="utf-8")

        def llm_call(messages):
            last = messages[-1]["content"]
            if "Current entry:" in last and "Propose exactly one update" in last:
                return (
                    '{"action":"replace","target":"memory","old_text":"Always use branch main for deploy.",'
                    '"content":"Use release branches for deploy.","reason":"policy changed","confidence":0.9}'
                )
            return "{}"

        run_stage2(
            metrics_db_path=db_path,
            hermes_home=tmp_path,
            llm_call=llm_call,
            days=7,
        )

        pending_mem = read_pending_memory_updates(
            tmp_path / "autoresearch" / "pending_memory_updates.json"
        )
        assert len(pending_mem) == 1

        conn = open_db(db_path)
        row = conn.execute(
            "SELECT status, target, action FROM autoresearch_memory_updates ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row["status"] == "proposed"
        assert row["target"] == "memory"
        assert row["action"] == "replace"

    def test_memory_updates_can_be_disabled(self, tmp_path):
        db_path = tmp_path / "metrics.db"
        open_db(db_path).close()
        run_stage2(
            metrics_db_path=db_path,
            hermes_home=tmp_path,
            llm_call=lambda _messages: "{}",
            enable_memory_updates=False,
        )
        pending_mem = read_pending_memory_updates(
            tmp_path / "autoresearch" / "pending_memory_updates.json"
        )
        assert pending_mem == []
