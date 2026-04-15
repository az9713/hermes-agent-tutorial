"""
Tests for cron/autoresearch/hypothesis_generator.py.

What we're testing
──────────────────
generate_hypothesis() calls an injected LLM callable, parses the JSON response,
validates that old_string exists in the skill content, and returns a CandidatePatch.
These tests verify:

1. A well-formed LLM response produces the expected CandidatePatch.
2. A null-patch response (LLM says no fix possible) returns None.
3. An unparseable LLM response returns None.
4. A patch where old_string is not in skill_content returns None (safety check).
5. Markdown-fenced JSON is parsed correctly.
6. Both trigger_metrics (correction + completion) are reflected in the output.
7. The LLM receives both a system and a user message.
8. Session excerpts are included in the user message.
9. Empty session excerpts are handled gracefully.

Why these tests matter
──────────────────────
The hypothesis generator is the most expensive step in Stage 2 (LLM call).
Bad JSON parsing or a missing old_string safety check would let garbage patches
through to the self-play evaluator. These tests lock in the filtering logic
before any real LLM is involved.
"""

import json
from typing import List, Dict

import pytest

from cron.autoresearch.hypothesis_generator import generate_hypothesis


# ── Fixtures ──────────────────────────────────────────────────────────────────

SKILL_CONTENT = """\
# git-workflow

## Overview
Use git for version control. Always commit with clear messages.

## Rules
- Never commit directly to main.
- Always create a feature branch.
- Squash fixup commits before merging.
"""

ANOMALY = {
    "skill_name": "git-workflow",
    "anomaly_type": "UNDERPERFORMING",
    "trigger_metric": "correction_rate=0.41",
    "correction_rate": 0.41,
    "completion_rate": 0.80,
    "invocation_count": 7,
}


def make_patch_response(old_string: str, new_string: str, reason: str) -> str:
    return json.dumps({
        "patch": {
            "old_string": old_string,
            "new_string": new_string,
            "reason": reason,
        }
    })


def make_null_response(reason: str = "not enough evidence") -> str:
    return json.dumps({"patch": None, "reason": reason})


def make_llm(response: str):
    """Return a lambda that captures messages and returns the given string."""
    received: List = []

    def _call(messages):
        received.extend(messages)
        return response

    _call.received = received
    return _call


# ── Tests: successful patch ───────────────────────────────────────────────────

class TestSuccessfulPatch:
    def test_returns_candidate_patch(self):
        old = "Always create a feature branch."
        new = "Always create a feature branch. Use `git checkout -b feat/<name>`."
        llm = make_llm(make_patch_response(old, new, "Clarify branch naming"))

        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        assert result is not None
        assert result["skill_name"] == "git-workflow"
        assert result["old_string"] == old
        assert result["new_string"] == new
        assert result["reason"] == "Clarify branch naming"

    def test_anomaly_fields_propagated(self):
        old = "Always create a feature branch."
        new = old + " (use descriptive names)"
        llm = make_llm(make_patch_response(old, new, "Improve branch naming guidance"))

        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        assert result["anomaly_type"] == "UNDERPERFORMING"
        assert result["trigger_metric"] == "correction_rate=0.41"

    def test_raw_llm_output_included(self):
        old = "Always create a feature branch."
        new = old + " (use descriptive names)"
        response = make_patch_response(old, new, "reason")
        llm = make_llm(response)

        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        assert result["raw_llm_output"] == response

    def test_fenced_json_parsed(self):
        """LLM wraps response in ```json ... ``` fences."""
        old = "Always create a feature branch."
        new = old + " (use descriptive names)"
        raw = "```json\n" + make_patch_response(old, new, "reason") + "\n```"
        llm = make_llm(raw)

        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        assert result is not None
        assert result["old_string"] == old


# ── Tests: None returns ───────────────────────────────────────────────────────

class TestNoneReturns:
    def test_null_patch_returns_none(self):
        llm = make_llm(make_null_response("not enough evidence"))
        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)
        assert result is None

    def test_unparseable_response_returns_none(self):
        llm = make_llm("I could not find an issue with this skill.")
        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)
        assert result is None

    def test_old_string_not_in_skill_returns_none(self):
        """LLM hallucinates text that isn't in the current SKILL.md."""
        old = "This text does not appear in the skill content."
        new = "Replacement text."
        llm = make_llm(make_patch_response(old, new, "reason"))

        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        assert result is None

    def test_empty_old_string_returns_none(self):
        response = json.dumps({"patch": {"old_string": "", "new_string": "x", "reason": "y"}})
        llm = make_llm(response)
        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)
        assert result is None

    def test_missing_patch_key_returns_none(self):
        llm = make_llm(json.dumps({"something_else": "value"}))
        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)
        assert result is None


# ── Tests: LLM message structure ─────────────────────────────────────────────

class TestLlmMessages:
    def test_system_message_sent(self):
        old = "Always create a feature branch."
        llm = make_llm(make_patch_response(old, old + " x", "r"))

        generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        roles = [m["role"] for m in llm.received]
        assert "system" in roles

    def test_user_message_sent(self):
        old = "Always create a feature branch."
        llm = make_llm(make_patch_response(old, old + " x", "r"))

        generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        roles = [m["role"] for m in llm.received]
        assert "user" in roles

    def test_skill_name_in_user_message(self):
        old = "Always create a feature branch."
        llm = make_llm(make_patch_response(old, old + " x", "r"))

        generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        user_msg = next(m for m in llm.received if m["role"] == "user")
        assert "git-workflow" in user_msg["content"]

    def test_trigger_metric_in_user_message(self):
        old = "Always create a feature branch."
        llm = make_llm(make_patch_response(old, old + " x", "r"))

        generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        user_msg = next(m for m in llm.received if m["role"] == "user")
        assert "correction_rate=0.41" in user_msg["content"]

    def test_skill_content_in_user_message(self):
        old = "Always create a feature branch."
        llm = make_llm(make_patch_response(old, old + " x", "r"))

        generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        user_msg = next(m for m in llm.received if m["role"] == "user")
        assert "git-workflow" in user_msg["content"]
        assert "Squash fixup commits" in user_msg["content"]


# ── Tests: session excerpts ───────────────────────────────────────────────────

class TestSessionExcerpts:
    def test_excerpts_included_in_user_message(self):
        old = "Always create a feature branch."
        excerpts = ["User: that's wrong, try again", "User: not what I asked for"]
        llm = make_llm(make_patch_response(old, old + " x", "r"))

        generate_hypothesis(ANOMALY, SKILL_CONTENT, excerpts, llm)

        user_msg = next(m for m in llm.received if m["role"] == "user")
        assert "try again" in user_msg["content"]

    def test_empty_excerpts_handled_gracefully(self):
        old = "Always create a feature branch."
        llm = make_llm(make_patch_response(old, old + " x", "r"))

        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)

        assert result is not None  # no crash

    def test_none_excerpts_not_accepted(self):
        """Function only accepts list — pass [] not None."""
        old = "Always create a feature branch."
        llm = make_llm(make_patch_response(old, old + " x", "r"))
        # Pass [] to verify empty list works; this is the documented contract
        result = generate_hypothesis(ANOMALY, SKILL_CONTENT, [], llm)
        assert result is not None
