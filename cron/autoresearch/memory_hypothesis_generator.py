"""
memory_hypothesis_generator.py -- LLM-backed memory update proposals.

Produces proposals constrained to built-in memory operations:
  - replace(target, old_text, content)
  - remove(target, old_text)
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

LlmCall = Callable[[List[Dict[str, str]]], str]

SYSTEM_PROMPT = """\
You are improving an AI assistant's persistent memory quality.
Given a possibly stale memory entry and evidence snippets, propose either:
- replace: update the stale entry with corrected content
- remove: delete the stale entry if it is obsolete/incorrect

Respond with JSON only:
{
  "action": "replace" | "remove",
  "target": "memory" | "user",
  "old_text": "<short unique substring of current entry>",
  "content": "<new content for replace; empty for remove>",
  "reason": "<one sentence>",
  "confidence": <number 0..1>
}

Do not propose "add". Be conservative when evidence is weak.
"""

USER_TEMPLATE = """\
Target: {target}
Current entry:
{entry_text}

Evidence snippets (recent user corrections):
{evidence}

Propose exactly one update for this entry.
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    stripped = re.sub(r"```(?:json)?\s*", "", text).strip()
    stripped = re.sub(r"```\s*$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _normalize_proposal(
    anomaly: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    min_confidence: float,
    min_evidence: int,
    min_evidence_score: float,
) -> Optional[Dict[str, Any]]:
    action = str(parsed.get("action", "")).strip().lower()
    target = str(parsed.get("target", anomaly.get("target", ""))).strip().lower()
    old_text = str(parsed.get("old_text", "")).strip()
    content = str(parsed.get("content", "")).strip()
    reason = str(parsed.get("reason", "")).strip()
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except Exception:
        return None

    if action not in ("replace", "remove"):
        return None
    if target not in ("memory", "user"):
        return None
    if not old_text:
        return None
    entry_text = str(anomaly.get("entry_text", ""))
    # Prevent proposing updates unrelated to the matched entry.
    if old_text not in entry_text and entry_text[: min(len(entry_text), 80)] not in old_text:
        return None
    if action == "replace" and not content:
        return None
    if action == "remove":
        content = ""
    if confidence < min_confidence:
        return None
    if int(anomaly.get("evidence_count", 0)) < min_evidence:
        return None
    if float(anomaly.get("weighted_evidence_score", 0.0)) < min_evidence_score:
        return None

    return {
        "target": target,
        "action": action,
        "old_text": old_text,
        "content": content,
        "reason": reason or "autoresearch memory update",
        "confidence": confidence,
        "evidence_count": int(anomaly.get("evidence_count", 0)),
        "evidence_score": float(anomaly.get("weighted_evidence_score", 0.0)),
        "evidence_snippets": anomaly.get("evidence_snippets", []),
        "trigger_metric": anomaly.get("trigger_metric", ""),
    }


def generate_memory_proposals(
    anomalies: List[Dict[str, Any]],
    llm_call: LlmCall,
    *,
    min_confidence: float = 0.7,
    min_evidence: int = 2,
    min_evidence_score: float = 1.25,
) -> List[Dict[str, Any]]:
    """Generate validated memory update proposals from anomaly inputs."""
    proposals: List[Dict[str, Any]] = []
    for anomaly in anomalies:
        evidence_block = "\n".join(f"- {s}" for s in anomaly.get("evidence_snippets", []))
        user_prompt = USER_TEMPLATE.format(
            target=anomaly.get("target", "memory"),
            entry_text=anomaly.get("entry_text", ""),
            evidence=evidence_block or "- (none)",
        )
        raw = llm_call(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        parsed = _extract_json(raw)
        if not parsed:
            continue
        proposal = _normalize_proposal(
            anomaly,
            parsed,
            min_confidence=min_confidence,
            min_evidence=min_evidence,
            min_evidence_score=min_evidence_score,
        )
        if proposal:
            proposals.append(proposal)
    return proposals
