"""
Tests for cron/autoresearch/pending_patches.py.

What we're testing
──────────────────
write_pending_patches() serialises (candidate, eval_result) pairs into a JSON
file. read_pending_patches() deserialises it. These tests verify:

1. write_pending_patches() creates the file at the specified path.
2. The JSON contains all required keys for each entry.
3. accepted/rejected/hold status is preserved correctly.
4. old_string/new_string/reason from the candidate are preserved.
5. token_delta, quality_delta, judge_scores from eval_result are preserved.
6. generated_at is a valid UTC ISO-8601 timestamp.
7. Parent directories are created automatically.
8. write_pending_patches() returns the JSON text.
9. read_pending_patches() returns an empty list for a missing file.
10. read_pending_patches() returns the list of entries from a written file.
11. Multiple entries are all written to the same file.
12. An empty list produces a valid empty JSON array.

Why these tests matter
──────────────────────
pending_patches.json is the hand-off between Stage 2 (evaluate) and Stage 3
(apply). Any dropped field or malformed JSON would silently break Stage 3.
These tests lock in the contract at the file boundary.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cron.autoresearch.pending_patches import read_pending_patches, write_pending_patches


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_candidate(
    skill_name: str = "git-workflow",
    old_string: str = "Always create a feature branch.",
    new_string: str = "Always create a feature branch named feat/<id>.",
    reason: str = "Clarify naming convention",
    trigger_metric: str = "correction_rate=0.41",
) -> dict:
    return {
        "skill_name": skill_name,
        "anomaly_type": "UNDERPERFORMING",
        "trigger_metric": trigger_metric,
        "old_string": old_string,
        "new_string": new_string,
        "reason": reason,
        "raw_llm_output": '{"patch": {...}}',
    }


def make_eval(
    status: str = "accepted",
    token_delta: float = -0.12,
    quality_delta: float = 0.40,
    judge_scores=None,
) -> dict:
    return {
        "accepted": status == "accepted",
        "status": status,
        "token_delta": token_delta,
        "quality_delta": quality_delta,
        "judge_scores": judge_scores or [[7.0, 8.0], [6.5, 7.5]],
        "hold_reason": "judge disagreement" if status == "hold" else "",
        "rejection_reason": "token_delta >= 0" if status == "rejected" else "",
    }


def make_pair(skill_name: str = "git-workflow", status: str = "accepted") -> dict:
    return {
        "candidate": make_candidate(skill_name=skill_name),
        "eval_result": make_eval(status=status),
    }


# ── Tests: write ──────────────────────────────────────────────────────────────

class TestWritePendingPatches:
    def test_file_created(self, tmp_path):
        path = tmp_path / "pending_patches.json"
        write_pending_patches([make_pair()], path=path)
        assert path.exists()

    def test_returns_json_text(self, tmp_path):
        path = tmp_path / "pending_patches.json"
        text = write_pending_patches([make_pair()], path=path)
        parsed = json.loads(text)
        assert isinstance(parsed, list)

    def test_json_matches_file_content(self, tmp_path):
        path = tmp_path / "pending_patches.json"
        text = write_pending_patches([make_pair()], path=path)
        assert path.read_text(encoding="utf-8") == text

    def test_parent_directories_created(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "pending_patches.json"
        write_pending_patches([make_pair()], path=nested)
        assert nested.exists()

    def test_empty_list_produces_empty_array(self, tmp_path):
        path = tmp_path / "pending_patches.json"
        text = write_pending_patches([], path=path)
        assert json.loads(text) == []

    def test_multiple_entries_written(self, tmp_path):
        path = tmp_path / "pending_patches.json"
        pairs = [make_pair("skill-a"), make_pair("skill-b"), make_pair("skill-c")]
        write_pending_patches(pairs, path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 3


class TestEntryFields:
    def _entry(self, tmp_path, skill_name="git-workflow", status="accepted"):
        path = tmp_path / "pp.json"
        write_pending_patches([make_pair(skill_name, status)], path=path)
        return json.loads(path.read_text(encoding="utf-8"))[0]

    def test_required_fields_present(self, tmp_path):
        entry = self._entry(tmp_path)
        required = {
            "skill_name", "anomaly_type", "trigger_metric", "action",
            "status", "accepted", "token_delta", "quality_delta",
            "judge_scores", "old_string", "new_string", "reason",
            "hold_reason", "rejection_reason", "generated_at",
        }
        assert required.issubset(set(entry.keys()))

    def test_skill_name_preserved(self, tmp_path):
        entry = self._entry(tmp_path, skill_name="web-search")
        assert entry["skill_name"] == "web-search"

    def test_accepted_status(self, tmp_path):
        entry = self._entry(tmp_path, status="accepted")
        assert entry["status"] == "accepted"
        assert entry["accepted"] is True

    def test_rejected_status(self, tmp_path):
        entry = self._entry(tmp_path, status="rejected")
        assert entry["status"] == "rejected"
        assert entry["accepted"] is False

    def test_hold_status(self, tmp_path):
        entry = self._entry(tmp_path, status="hold")
        assert entry["status"] == "hold"
        assert entry["accepted"] is False
        assert entry["hold_reason"] != ""

    def test_old_string_preserved(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["old_string"] == "Always create a feature branch."

    def test_new_string_preserved(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["new_string"] == "Always create a feature branch named feat/<id>."

    def test_reason_preserved(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["reason"] == "Clarify naming convention"

    def test_token_delta_preserved(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["token_delta"] == pytest.approx(-0.12)

    def test_quality_delta_preserved(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["quality_delta"] == pytest.approx(0.40)

    def test_judge_scores_preserved(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["judge_scores"] == [[7.0, 8.0], [6.5, 7.5]]

    def test_action_is_patch(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["action"] == "patch"

    def test_generated_at_is_utc_iso8601(self, tmp_path):
        entry = self._entry(tmp_path)
        ts = entry["generated_at"]
        # Must match YYYY-MM-DDTHH:MM:SSZ
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), (
            f"generated_at not in UTC ISO-8601 format: {ts}"
        )

    def test_trigger_metric_preserved(self, tmp_path):
        entry = self._entry(tmp_path)
        assert entry["trigger_metric"] == "correction_rate=0.41"


# ── Tests: read ───────────────────────────────────────────────────────────────

class TestReadPendingPatches:
    def test_missing_file_returns_empty_list(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        result = read_pending_patches(path=path)
        assert result == []

    def test_reads_written_file(self, tmp_path):
        path = tmp_path / "pp.json"
        write_pending_patches([make_pair("skill-a"), make_pair("skill-b")], path=path)
        result = read_pending_patches(path=path)
        assert len(result) == 2

    def test_returns_list_of_dicts(self, tmp_path):
        path = tmp_path / "pp.json"
        write_pending_patches([make_pair()], path=path)
        result = read_pending_patches(path=path)
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    def test_corrupted_file_returns_empty_list(self, tmp_path):
        path = tmp_path / "pp.json"
        path.write_text("not valid json", encoding="utf-8")
        result = read_pending_patches(path=path)
        assert result == []

    def test_skill_name_round_trips(self, tmp_path):
        path = tmp_path / "pp.json"
        write_pending_patches([make_pair("my-special-skill")], path=path)
        result = read_pending_patches(path=path)
        assert result[0]["skill_name"] == "my-special-skill"
