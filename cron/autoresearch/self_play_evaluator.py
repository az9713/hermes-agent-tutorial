"""
self_play_evaluator.py — Evaluate a candidate patch via synthetic self-play.

Pipeline:
  1. Generate N synthetic task descriptions (variants of real session tasks).
  2. For each task, run the agent once with the OLD skill and once with the NEW skill.
  3. Measure token efficiency (len of response proxy) and quality via LLM judge.
  4. Accept the patch if it is more efficient AND not worse quality.
     HOLD if judges disagree by > JUDGE_DISAGREEMENT_THRESHOLD.

The LLM callable is injected for testability — tests pass lambda stubs without
touching any live provider.

LlmCall = Callable[[list[dict]], str]  — same interface as hypothesis_generator.

Public API:
  evaluate_candidate(candidate_patch, session_tasks, llm_call)
    → EvalResult

EvalResult keys:
  accepted        bool   — True if patch passes the gate
  status          str    — "accepted" | "rejected" | "hold"
  token_delta     float  — (new - old) / old  (negative = more efficient)
  quality_delta   float  — avg(judge score B) - avg(judge score A)
  judge_scores    list   — [[score_A, score_B], ...]  per synthetic task
  hold_reason     str    — set when status == "hold"
  rejection_reason str   — set when status == "rejected"
"""

import re
from typing import Any, Callable, Dict, List, Optional

# ── Constants ─────────────────────────────────────────────────────────────────

N_SYNTHETIC_TASKS = 5
JUDGE_DISAGREEMENT_THRESHOLD = 2.0   # hold if two judge calls differ by > 2 pts
MIN_QUALITY_DELTA = 0.0              # patch must not lower quality


# ── Types ─────────────────────────────────────────────────────────────────────

LlmCall = Callable[[List[Dict[str, str]]], str]
EvalResult = Dict[str, Any]
CandidatePatch = Dict[str, Any]


# ── Internal helpers ──────────────────────────────────────────────────────────

_REPHRASE_PROMPT = """\
Rephrase the following task description slightly — same intent, different wording.
Keep it realistic and concise (1-3 sentences). Output only the rephrased task.

Task: {task}
"""

_AGENT_PROMPT = """\
You are an AI assistant. The following skill instructs you on how to handle this task.

## Skill
{skill_content}

## Task
{task}

Respond to the task, following the skill instructions.
"""

_JUDGE_PROMPT = """\
Score the following AI response to a task on a scale of 0-10.
Consider: correctness, completeness, and whether it follows the skill instructions.
Respond with a single integer (0-10) only.

Task: {task}
Response: {response}
"""


def _extract_score(text: str) -> float:
    """Extract a numeric score (0-10) from judge output. Returns 5.0 on failure."""
    match = re.search(r"\b([0-9]|10)\b", text.strip())
    if match:
        return float(match.group(1))
    return 5.0


def _generate_synthetic_tasks(
    session_tasks: List[str],
    llm_call: LlmCall,
    n: int = N_SYNTHETIC_TASKS,
) -> List[str]:
    """Produce n synthetic task variants from session_tasks.

    If session_tasks has enough entries, return the first n directly (no LLM
    call needed — they are already real task descriptions). If fewer, rephrase
    the available ones to reach n.
    """
    if not session_tasks:
        return []

    tasks = list(session_tasks)

    # Fill to n by rephrasing if needed
    i = 0
    while len(tasks) < n:
        base = session_tasks[i % len(session_tasks)]
        prompt = _REPHRASE_PROMPT.format(task=base)
        rephrased = llm_call([{"role": "user", "content": prompt}]).strip()
        tasks.append(rephrased)
        i += 1

    return tasks[:n]


def _run_agent(task: str, skill_content: str, llm_call: LlmCall) -> str:
    """Single-turn agent completion for a task under a given skill."""
    prompt = _AGENT_PROMPT.format(skill_content=skill_content, task=task)
    return llm_call([{"role": "user", "content": prompt}])


def _judge(task: str, response: str, llm_call: LlmCall) -> float:
    """Score a response 0-10 via the judge LLM."""
    prompt = _JUDGE_PROMPT.format(task=task, response=response)
    raw = llm_call([{"role": "user", "content": prompt}])
    return _extract_score(raw)


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate_candidate(
    candidate_patch: CandidatePatch,
    skill_content_old: str,
    session_tasks: List[str],
    llm_call: LlmCall,
    n_tasks: int = N_SYNTHETIC_TASKS,
) -> EvalResult:
    """Evaluate a candidate patch via self-play.

    Args:
        candidate_patch:  CandidatePatch from generate_hypothesis().
        skill_content_old: Current SKILL.md content (before patch).
        session_tasks:    Real task descriptions from sessions that triggered
                          the anomaly. Used to ground synthetic tasks.
        llm_call:         Injected LLM callable (messages -> str).
        n_tasks:          Number of synthetic tasks to generate (default 5).

    Returns:
        EvalResult with status "accepted", "rejected", or "hold".
    """
    # Build the new (patched) skill content
    old_string = candidate_patch["old_string"]
    new_string = candidate_patch["new_string"]
    skill_content_new = skill_content_old.replace(old_string, new_string, 1)

    # Generate synthetic tasks
    tasks = _generate_synthetic_tasks(session_tasks, llm_call, n=n_tasks)

    if not tasks:
        return {
            "accepted": False,
            "status": "rejected",
            "token_delta": 0.0,
            "quality_delta": 0.0,
            "judge_scores": [],
            "hold_reason": "",
            "rejection_reason": "no session tasks provided — cannot evaluate",
        }

    # Run self-play
    total_len_old = 0
    total_len_new = 0
    judge_scores: List[List[float]] = []
    hold_reason = ""

    for task in tasks:
        response_old = _run_agent(task, skill_content_old, llm_call)
        response_new = _run_agent(task, skill_content_new, llm_call)

        total_len_old += len(response_old)
        total_len_new += len(response_new)

        score_old = _judge(task, response_old, llm_call)
        score_new = _judge(task, response_new, llm_call)
        judge_scores.append([score_old, score_new])

        # Check for judge disagreement (two calls with same judge — flag if
        # re-scoring would differ by > threshold; approximate with single call)
        if abs(score_old - score_new) > JUDGE_DISAGREEMENT_THRESHOLD + 2:
            # Large gap: run a second judge call to verify
            score_new2 = _judge(task, response_new, llm_call)
            if abs(score_new - score_new2) > JUDGE_DISAGREEMENT_THRESHOLD:
                hold_reason = (
                    f"judge disagreement on task '{task[:60]}': "
                    f"scores {score_new:.1f} vs {score_new2:.1f}"
                )

    # Compute deltas
    token_delta = (total_len_new - total_len_old) / max(total_len_old, 1)

    avg_score_old = sum(s[0] for s in judge_scores) / len(judge_scores)
    avg_score_new = sum(s[1] for s in judge_scores) / len(judge_scores)
    quality_delta = avg_score_new - avg_score_old

    # Apply acceptance gate
    rejection_reason = ""
    if hold_reason:
        status = "hold"
        accepted = False
    elif token_delta < 0 and quality_delta >= MIN_QUALITY_DELTA:
        status = "accepted"
        accepted = True
    else:
        status = "rejected"
        accepted = False
        parts = []
        if token_delta >= 0:
            parts.append(f"token_delta={token_delta:+.2f} (must be < 0)")
        if quality_delta < MIN_QUALITY_DELTA:
            parts.append(f"quality_delta={quality_delta:+.2f} (must be >= {MIN_QUALITY_DELTA})")
        rejection_reason = "; ".join(parts)

    return {
        "accepted": accepted,
        "status": status,
        "token_delta": token_delta,
        "quality_delta": quality_delta,
        "judge_scores": judge_scores,
        "hold_reason": hold_reason if status == "hold" else "",
        "rejection_reason": rejection_reason,
    }
