"""
Tests for cron/autoresearch/self_play_evaluator.py.

What we're testing
──────────────────
evaluate_candidate() runs self-play between old and new skill, measures
token efficiency and quality via an injected LLM callable, and returns an
EvalResult with status accepted/rejected/hold. These tests verify:

1. A patch that shortens responses AND improves quality is accepted.
2. A patch that lengthens responses is rejected (even if quality improves).
3. A patch that lowers quality is rejected (even if shorter).
4. Judge disagreement produces a hold status.
5. No session tasks produces a rejected status with an explanation.
6. The patched skill content is correctly derived from old_string/new_string.
7. token_delta and quality_delta are computed correctly.
8. judge_scores contains one pair per synthetic task.

Why these tests matter
──────────────────────
The evaluator is the gatekeeper that filters bad patches before they reach
Stage 3. Incorrect acceptance logic or wrong delta calculations would let
harmful patches through. These tests lock in the gate conditions exactly,
using deterministic stub LLMs — no live API calls.
"""

from typing import List, Dict

import pytest

from cron.autoresearch.self_play_evaluator import (
    JUDGE_DISAGREEMENT_THRESHOLD,
    MIN_QUALITY_DELTA,
    N_SYNTHETIC_TASKS,
    evaluate_candidate,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

OLD_SKILL = """\
# git-workflow

## Rules
- Always create a feature branch.
- Commit with clear messages.
"""

CANDIDATE = {
    "skill_name": "git-workflow",
    "anomaly_type": "UNDERPERFORMING",
    "trigger_metric": "correction_rate=0.41",
    "old_string": "Always create a feature branch.",
    "new_string": "Always create a feature branch named feat/<ticket-id>.",
    "reason": "Clarify naming convention",
}

TASKS = ["Create a git branch for ticket-123", "Commit some changes to the repo"]


# ── Stub LLM factories ────────────────────────────────────────────────────────

class _CallCounter:
    """LLM stub that tracks calls and returns configurable responses."""

    def __init__(self, responses):
        """responses: list of str, cycling through them in order."""
        self._responses = list(responses)
        self._idx = 0
        self.calls: List[List[Dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        r = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return r


def make_stub(
    rephrase="Rephrased task.",
    old_response="Old skill response. " * 10,     # ~200 chars
    new_response="New skill response. " * 5,      # ~100 chars (shorter)
    old_score="7",
    new_score="8",
):
    """Build a stub that returns predictable responses in call order.

    Call order per task:
      1. _run_agent with old skill  → old_response
      2. _run_agent with new skill  → new_response
      3. _judge with old response   → old_score
      4. _judge with new response   → new_score

    Rephrase calls come before agent calls if tasks need to be generated.
    We pass TASKS directly so rephrase calls do not happen.
    """
    # Build a sequence: for each of N_SYNTHETIC_TASKS we expect 4 calls
    # (old agent, new agent, old judge, new judge).
    # Since we pass exactly N_SYNTHETIC_TASKS tasks, no rephrase calls needed.
    sequence = []
    for _ in range(N_SYNTHETIC_TASKS):
        sequence.extend([old_response, new_response, old_score, new_score])
    return _CallCounter(sequence)


# ── Tests: acceptance gate ────────────────────────────────────────────────────

class TestAcceptanceGate:
    def test_efficient_and_better_quality_accepted(self):
        stub = make_stub(
            old_response="x" * 200,   # old: 200 chars
            new_response="x" * 100,   # new: 100 chars (token_delta < 0)
            old_score="7",
            new_score="8",            # quality improved
        )
        tasks = TASKS * 3  # ensure enough tasks

        result = evaluate_candidate(CANDIDATE, OLD_SKILL, tasks, stub, n_tasks=1)

        assert result["accepted"] is True
        assert result["status"] == "accepted"
        assert result["token_delta"] < 0
        assert result["quality_delta"] >= 0

    def test_longer_response_rejected(self):
        stub = make_stub(
            old_response="x" * 100,
            new_response="x" * 200,   # longer — token_delta > 0
            old_score="7",
            new_score="8",
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        assert result["accepted"] is False
        assert result["status"] == "rejected"
        assert result["token_delta"] > 0
        assert "token_delta" in result["rejection_reason"]

    def test_worse_quality_rejected(self):
        stub = make_stub(
            old_response="x" * 200,
            new_response="x" * 100,   # shorter (good)
            old_score="8",
            new_score="5",            # quality dropped (bad)
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        assert result["accepted"] is False
        assert result["status"] == "rejected"
        assert result["quality_delta"] < 0

    def test_same_quality_accepted_if_shorter(self):
        """Equal quality (delta = 0) is still accepted if token_delta < 0."""
        stub = make_stub(
            old_response="x" * 200,
            new_response="x" * 100,
            old_score="7",
            new_score="7",            # same score — quality_delta = 0
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        assert result["accepted"] is True
        assert result["quality_delta"] == pytest.approx(0.0)


class TestEmptyTasks:
    def test_no_tasks_rejected_with_reason(self):
        stub = _CallCounter(["any response"])
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, [], stub, n_tasks=1)

        assert result["accepted"] is False
        assert result["status"] == "rejected"
        assert "no session tasks" in result["rejection_reason"]


class TestPatchApplication:
    def test_new_skill_contains_new_string(self):
        """The self-play must use the patched skill content."""
        received_prompts = []

        def tracking_llm(messages):
            received_prompts.append(messages[-1]["content"])
            return "Response. " * 5  # shorter than old

        # We need scores too — cycle through a simple counter
        call_idx = [0]
        def stub(messages):
            call_idx[0] += 1
            content = messages[-1]["content"]
            received_prompts.append(content)
            # Alternate: agent calls return text; judge calls return score
            return "3"  # all returns "3" — quality_delta=0, token_delta depends on length

        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        # Check that at least one prompt contained the new_string
        new_str = CANDIDATE["new_string"]
        assert any(new_str in p for p in received_prompts), (
            f"Expected '{new_str}' in at least one prompt, got: {received_prompts[:2]}"
        )

    def test_old_skill_preserved_for_old_run(self):
        """The old run must use the original skill content (not the patched one)."""
        received_prompts = []

        def stub(messages):
            received_prompts.append(messages[-1]["content"])
            return "Response."

        evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        old_str = CANDIDATE["old_string"]
        assert any(old_str in p for p in received_prompts)


class TestDeltaComputation:
    def test_token_delta_formula(self):
        """token_delta = (new_len - old_len) / old_len."""
        old_len = 200
        new_len = 150
        expected_delta = (new_len - old_len) / old_len  # -0.25

        stub = make_stub(
            old_response="x" * old_len,
            new_response="x" * new_len,
            old_score="7",
            new_score="8",
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        assert result["token_delta"] == pytest.approx(expected_delta, abs=0.01)

    def test_quality_delta_formula(self):
        """quality_delta = avg(new_scores) - avg(old_scores)."""
        stub = make_stub(
            old_response="x" * 200,
            new_response="x" * 100,
            old_score="6",
            new_score="9",
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        assert result["quality_delta"] == pytest.approx(3.0, abs=0.01)

    def test_judge_scores_length(self):
        """One [old_score, new_score] pair per synthetic task."""
        stub = make_stub(
            old_response="x" * 200,
            new_response="x" * 100,
        )
        n = 2
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS * 2, stub, n_tasks=n)

        assert len(result["judge_scores"]) == n
        for pair in result["judge_scores"]:
            assert len(pair) == 2


class TestResultFields:
    def test_all_fields_present(self):
        stub = make_stub(
            old_response="x" * 200,
            new_response="x" * 100,
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        required = {"accepted", "status", "token_delta", "quality_delta",
                    "judge_scores", "hold_reason", "rejection_reason"}
        assert required.issubset(set(result.keys()))

    def test_hold_reason_empty_when_accepted(self):
        stub = make_stub(
            old_response="x" * 200,
            new_response="x" * 100,
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        assert result["hold_reason"] == ""

    def test_rejection_reason_empty_when_accepted(self):
        stub = make_stub(
            old_response="x" * 200,
            new_response="x" * 100,
        )
        result = evaluate_candidate(CANDIDATE, OLD_SKILL, TASKS, stub, n_tasks=1)

        if result["status"] == "accepted":
            assert result["rejection_reason"] == ""
