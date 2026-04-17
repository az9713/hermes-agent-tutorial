"""
self_play_evaluator.py -- Evaluate candidate patches via self-play + holdout.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

LlmCall = Callable[[List[Dict[str, str]]], str]
EvalResult = Dict[str, Any]
CandidatePatch = Dict[str, Any]

N_SYNTHETIC_TASKS = 5
JUDGE_DISAGREEMENT_THRESHOLD = 2.0
DUAL_JUDGE_DELTA_DISAGREEMENT = 1.5
MIN_QUALITY_DELTA = 0.0
MIN_HOLDOUT_QUALITY_DELTA = 0.0

_REPHRASE_PROMPT = """\
Rephrase the following task description slightly -- same intent, different wording.
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
    match = re.search(r"\b([0-9]|10)\b", text.strip())
    if match:
        return float(match.group(1))
    return 5.0


def _generate_synthetic_tasks(
    session_tasks: List[str],
    llm_call: LlmCall,
    n: int = N_SYNTHETIC_TASKS,
) -> List[str]:
    if not session_tasks:
        return []
    tasks = list(session_tasks)
    i = 0
    while len(tasks) < n:
        base = session_tasks[i % len(session_tasks)]
        prompt = _REPHRASE_PROMPT.format(task=base)
        rephrased = llm_call([{"role": "user", "content": prompt}]).strip()
        tasks.append(rephrased)
        i += 1
    return tasks[:n]


def _run_agent(task: str, skill_content: str, llm_call: LlmCall) -> str:
    prompt = _AGENT_PROMPT.format(skill_content=skill_content, task=task)
    return llm_call([{"role": "user", "content": prompt}])


def _judge(task: str, response: str, llm_call: LlmCall) -> float:
    prompt = _JUDGE_PROMPT.format(task=task, response=response)
    raw = llm_call([{"role": "user", "content": prompt}])
    return _extract_score(raw)


def _rubric_pass(task: str, response: str) -> bool:
    """Deterministic lightweight quality rubric for offline adjudication."""
    text = (response or "").strip()
    if len(text) < 20:
        return False
    words = re.findall(r"\w+", text)
    if len(words) < 5:
        return False
    lowered = text.lower()
    for banned in ("todo", "tbd", "lorem ipsum", "i cannot comply"):
        if banned in lowered:
            return False
    if "?" in task and len(words) < 10:
        return False
    return True


def _evaluate_task_set(
    tasks: List[str],
    *,
    skill_content_old: str,
    skill_content_new: str,
    llm_call: LlmCall,
    judge_llm_call: Optional[LlmCall],
) -> Dict[str, Any]:
    total_len_old = 0
    total_len_new = 0
    primary_scores: List[List[float]] = []
    secondary_deltas: List[float] = []
    dual_disagreement = False
    disagreement_note = ""
    rubric_old = 0
    rubric_new = 0

    for task in tasks:
        response_old = _run_agent(task, skill_content_old, llm_call)
        response_new = _run_agent(task, skill_content_new, llm_call)

        total_len_old += len(response_old)
        total_len_new += len(response_new)

        p_old = _judge(task, response_old, llm_call)
        p_new = _judge(task, response_new, llm_call)
        primary_scores.append([p_old, p_new])

        primary_delta = p_new - p_old
        if judge_llm_call is not None:
            s_old = _judge(task, response_old, judge_llm_call)
            s_new = _judge(task, response_new, judge_llm_call)
            secondary_delta = s_new - s_old
            secondary_deltas.append(secondary_delta)
            if abs(primary_delta - secondary_delta) > DUAL_JUDGE_DELTA_DISAGREEMENT:
                dual_disagreement = True
                disagreement_note = (
                    f"dual-judge disagreement on task '{task[:60]}': "
                    f"primary_delta={primary_delta:+.1f} secondary_delta={secondary_delta:+.1f}"
                )

        if _rubric_pass(task, response_old):
            rubric_old += 1
        if _rubric_pass(task, response_new):
            rubric_new += 1

    token_delta = (total_len_new - total_len_old) / max(total_len_old, 1)
    avg_primary_old = sum(s[0] for s in primary_scores) / len(primary_scores)
    avg_primary_new = sum(s[1] for s in primary_scores) / len(primary_scores)
    primary_quality_delta = avg_primary_new - avg_primary_old
    secondary_quality_delta = (
        sum(secondary_deltas) / len(secondary_deltas)
        if secondary_deltas
        else primary_quality_delta
    )
    # Backward-compatible contract: quality_delta tracks primary judge delta.
    quality_delta = primary_quality_delta
    rubric_pass_rate_old = rubric_old / max(len(tasks), 1)
    rubric_pass_rate_new = rubric_new / max(len(tasks), 1)

    return {
        "token_delta": token_delta,
        "quality_delta": quality_delta,
        "primary_quality_delta": primary_quality_delta,
        "secondary_quality_delta": secondary_quality_delta,
        "judge_scores": primary_scores,
        "dual_disagreement": dual_disagreement,
        "disagreement_note": disagreement_note,
        "rubric_pass_rate_old": rubric_pass_rate_old,
        "rubric_pass_rate_new": rubric_pass_rate_new,
    }


def evaluate_candidate(
    candidate_patch: CandidatePatch,
    skill_content_old: str,
    session_tasks: List[str],
    llm_call: LlmCall,
    n_tasks: int = N_SYNTHETIC_TASKS,
    holdout_tasks: Optional[List[str]] = None,
    judge_llm_call: Optional[LlmCall] = None,
) -> EvalResult:
    """Evaluate a candidate patch via self-play and holdout/rubric checks."""
    old_string = candidate_patch["old_string"]
    new_string = candidate_patch["new_string"]
    skill_content_new = skill_content_old.replace(old_string, new_string, 1)

    tasks = _generate_synthetic_tasks(session_tasks, llm_call, n=n_tasks)
    if not tasks:
        return {
            "accepted": False,
            "status": "rejected",
            "token_delta": 0.0,
            "quality_delta": 0.0,
            "judge_scores": [],
            "hold_reason": "",
            "rejection_reason": "no session tasks provided -- cannot evaluate",
            "holdout_task_count": 0,
            "holdout_token_delta": 0.0,
            "holdout_quality_delta": 0.0,
            "holdout_pass": False,
            "rubric_pass_rate_old": 0.0,
            "rubric_pass_rate_new": 0.0,
            "holdout_rubric_pass_rate_old": 0.0,
            "holdout_rubric_pass_rate_new": 0.0,
            "dual_judge_disagreement": False,
            "primary_quality_delta": 0.0,
            "secondary_quality_delta": 0.0,
        }

    selfplay = _evaluate_task_set(
        tasks,
        skill_content_old=skill_content_old,
        skill_content_new=skill_content_new,
        llm_call=llm_call,
        judge_llm_call=judge_llm_call,
    )

    holdout = {
        "token_delta": 0.0,
        "quality_delta": 0.0,
        "rubric_pass_rate_old": 0.0,
        "rubric_pass_rate_new": 0.0,
        "dual_disagreement": False,
        "disagreement_note": "",
        "task_count": 0,
        "pass": True,
    }
    if holdout_tasks:
        used_holdout = [t.strip() for t in holdout_tasks if t and t.strip()]
        if used_holdout:
            h = _evaluate_task_set(
                used_holdout,
                skill_content_old=skill_content_old,
                skill_content_new=skill_content_new,
                llm_call=llm_call,
                judge_llm_call=judge_llm_call,
            )
            holdout = {
                "token_delta": h["token_delta"],
                "quality_delta": h["quality_delta"],
                "rubric_pass_rate_old": h["rubric_pass_rate_old"],
                "rubric_pass_rate_new": h["rubric_pass_rate_new"],
                "dual_disagreement": h["dual_disagreement"],
                "disagreement_note": h["disagreement_note"],
                "task_count": len(used_holdout),
                "pass": h["quality_delta"] >= MIN_HOLDOUT_QUALITY_DELTA,
            }

    hold_reason = ""
    if selfplay["dual_disagreement"] or holdout["dual_disagreement"]:
        hold_reason = selfplay["disagreement_note"] or holdout["disagreement_note"]

    rejection_parts: List[str] = []
    if selfplay["token_delta"] >= 0:
        rejection_parts.append(
            f"token_delta={selfplay['token_delta']:+.2f} (must be < 0)"
        )
    if selfplay["quality_delta"] < MIN_QUALITY_DELTA:
        rejection_parts.append(
            f"quality_delta={selfplay['quality_delta']:+.2f} (must be >= {MIN_QUALITY_DELTA})"
        )
    if holdout["task_count"] > 0 and not holdout["pass"]:
        rejection_parts.append(
            f"holdout_quality_delta={holdout['quality_delta']:+.2f} (must be >= {MIN_HOLDOUT_QUALITY_DELTA})"
        )
    if selfplay["rubric_pass_rate_new"] < selfplay["rubric_pass_rate_old"]:
        rejection_parts.append(
            f"rubric_pass_rate dropped {selfplay['rubric_pass_rate_old']:.2f} -> {selfplay['rubric_pass_rate_new']:.2f}"
        )
    if holdout["task_count"] > 0 and holdout["rubric_pass_rate_new"] < holdout["rubric_pass_rate_old"]:
        rejection_parts.append(
            f"holdout rubric_pass_rate dropped {holdout['rubric_pass_rate_old']:.2f} -> {holdout['rubric_pass_rate_new']:.2f}"
        )

    if hold_reason:
        status = "hold"
        accepted = False
        rejection_reason = ""
    elif not rejection_parts:
        status = "accepted"
        accepted = True
        rejection_reason = ""
    else:
        status = "rejected"
        accepted = False
        rejection_reason = "; ".join(rejection_parts)

    return {
        "accepted": accepted,
        "status": status,
        "token_delta": selfplay["token_delta"],
        "quality_delta": selfplay["quality_delta"],
        "judge_scores": selfplay["judge_scores"],
        "hold_reason": hold_reason if status == "hold" else "",
        "rejection_reason": rejection_reason,
        "holdout_task_count": holdout["task_count"],
        "holdout_token_delta": holdout["token_delta"],
        "holdout_quality_delta": holdout["quality_delta"],
        "holdout_pass": bool(holdout["pass"]),
        "rubric_pass_rate_old": selfplay["rubric_pass_rate_old"],
        "rubric_pass_rate_new": selfplay["rubric_pass_rate_new"],
        "holdout_rubric_pass_rate_old": holdout["rubric_pass_rate_old"],
        "holdout_rubric_pass_rate_new": holdout["rubric_pass_rate_new"],
        "dual_judge_disagreement": bool(selfplay["dual_disagreement"] or holdout["dual_disagreement"]),
        "primary_quality_delta": selfplay["primary_quality_delta"],
        "secondary_quality_delta": selfplay["secondary_quality_delta"],
    }
