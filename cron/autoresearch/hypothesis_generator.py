"""
hypothesis_generator.py -- Generate targeted skill patches from anomalies.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

LlmCall = Callable[[List[Dict[str, str]]], str]

CandidatePatch = Dict[str, Any]
"""
Keys:
  skill_name      str
  anomaly_type    str
  trigger_metric  str
  old_string      str
  new_string      str
  reason          str
  raw_llm_output  str
"""

_SYSTEM_PROMPT = """\
You are a skill improvement expert for an AI agent system called Hermes.
Hermes uses Markdown "skill" files (SKILL.md) that instruct the agent how to
handle specific task types.

Your job: analyse evidence from real sessions where this skill performed
poorly, identify the specific gap, and propose a minimal targeted patch.

Anomaly guidance:
- UNDERPERFORMING: tighten clarity and remove avoidable correction loops.
- STRUCTURALLY_BROKEN: repair broken flow/ordering and reduce inefficiency.
- MISSING_COVERAGE: add explicit missing branches/examples for repeated correction scenarios.

Rules for your patch:
1. old_string must appear verbatim in the current SKILL.md. Quote it exactly.
2. new_string is the replacement -- improve or clarify the problematic section.
3. The patch must be minimal: change only what is needed to fix the gap.
4. Do not rewrite the entire skill. Only patch the specific problem.
5. If you cannot identify a specific gap (not enough evidence), respond with:
   {"patch": null, "reason": "<explanation of why no patch is possible>"}
"""

_USER_TEMPLATE = """\
## Skill: {skill_name}

### Anomaly
- Type: {anomaly_type}
- Trigger: {trigger_metric}
- Correction rate (7-day): {correction_rate:.0%}
- Completion rate (7-day): {completion_rate:.0%}
- Invocations: {invocation_count}

### Current SKILL.md content
```
{skill_content}
```

### Session excerpts (worst sessions -- user corrections only)
{excerpts_block}

### Instructions
Identify the specific gap in the skill that caused these corrections.
Propose a minimal patch.

Respond with valid JSON only:
{{
  "patch": {{
    "old_string": "<exact text from SKILL.md>",
    "new_string": "<replacement text>",
    "reason": "<one sentence explaining what this fixes>"
  }}
}}

Or if no patch is identifiable:
{{
  "patch": null,
  "reason": "<why no patch is possible>"
}}
"""


def _format_excerpts(session_excerpts: List[str]) -> str:
    if not session_excerpts:
        return "_No excerpts provided._"
    lines = []
    for i, excerpt in enumerate(session_excerpts, 1):
        lines.append(f"**Session {i}:**")
        lines.append(excerpt.strip())
        lines.append("")
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    stripped = re.sub(r"```(?:json)?\s*", "", text).strip()
    stripped = re.sub(r"```\s*$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def generate_hypothesis(
    anomaly: Dict[str, Any],
    skill_content: str,
    session_excerpts: List[str],
    llm_call: LlmCall,
) -> Optional[CandidatePatch]:
    """Ask the LLM to propose a patch for an anomalous skill."""
    user_content = _USER_TEMPLATE.format(
        skill_name=anomaly["skill_name"],
        anomaly_type=anomaly["anomaly_type"],
        trigger_metric=anomaly["trigger_metric"],
        correction_rate=anomaly["correction_rate"],
        completion_rate=anomaly["completion_rate"],
        invocation_count=anomaly["invocation_count"],
        skill_content=skill_content,
        excerpts_block=_format_excerpts(session_excerpts),
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    raw_output = llm_call(messages)
    parsed = _extract_json(raw_output)
    if parsed is None:
        return None
    patch_data = parsed.get("patch")
    if patch_data is None:
        return None

    old_string = patch_data.get("old_string", "")
    new_string = patch_data.get("new_string", "")
    reason = patch_data.get("reason", "")
    if not old_string or old_string not in skill_content:
        return None

    return {
        "skill_name": anomaly["skill_name"],
        "anomaly_type": anomaly["anomaly_type"],
        "trigger_metric": anomaly["trigger_metric"],
        "old_string": old_string,
        "new_string": new_string,
        "reason": reason,
        "raw_llm_output": raw_output,
    }
