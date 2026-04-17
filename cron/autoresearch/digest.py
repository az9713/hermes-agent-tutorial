"""
digest.py -- Format a human-readable nightly digest for Stage 3.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home


def get_default_digest_path() -> Path:
    return get_hermes_home() / "autoresearch" / "nightly_digest.md"


def _section_applied(apply_results: List[Dict[str, Any]]) -> str:
    applied = [r for r in apply_results if r["status"] == "applied"]
    if not applied:
        return "_No patches applied this cycle._\n"
    return "\n".join(f"- **{r['skill_name']}**: {r['reason']}" for r in applied) + "\n"


def _section_deferred(apply_results: List[Dict[str, Any]]) -> str:
    deferred = [r for r in apply_results if r["status"] == "deferred"]
    if not deferred:
        return "_No patches deferred._\n"
    return "\n".join(f"- **{r['skill_name']}**: {r['reason']}" for r in deferred) + "\n"


def _section_rejected(pending_patches: List[Dict[str, Any]]) -> str:
    rejected = [p for p in pending_patches if p.get("status") == "rejected"]
    if not rejected:
        return "_No patches rejected by self-play._\n"
    lines = []
    for p in rejected:
        td = p.get("token_delta", 0)
        qd = p.get("quality_delta", 0)
        lines.append(
            f"- **{p['skill_name']}**: token_delta={td:+.0%}, "
            f"quality_delta={qd:+.1f} - {p.get('rejection_reason', '')}"
        )
    return "\n".join(lines) + "\n"


def _section_regression_watch(watch_results: List[Dict[str, Any]]) -> str:
    if not watch_results:
        return "_No patches under regression watch this cycle._\n"
    lines = []
    for r in watch_results:
        status = r["status"]
        if status == "stable":
            symbol = "✓"
        elif status == "rolled_back":
            symbol = "↩"
        else:
            symbol = "⚠"
        lines.append(f"- {symbol} **{r['skill_name']}**: {r['reason']}")
    return "\n".join(lines) + "\n"


def _section_memory_proposed(memory_proposed: List[Dict[str, Any]]) -> str:
    if not memory_proposed:
        return "_No memory updates queued._\n"
    lines = []
    for m in memory_proposed:
        lines.append(
            f"- **{m.get('target', '?')}** {m.get('action', '?')}: "
            f"confidence={float(m.get('confidence', 0.0)):.2f}, evidence={int(m.get('evidence_count', 0))}"
        )
    return "\n".join(lines) + "\n"


def _section_memory_applied(memory_applied: List[Dict[str, Any]]) -> str:
    if not memory_applied:
        return "_No memory updates applied this cycle._\n"
    lines = []
    for m in memory_applied:
        lines.append(
            f"- **{m.get('target', '?')}** {m.get('action', '?')}: {m.get('reason', '')}"
        )
    return "\n".join(lines) + "\n"


def _section_memory_needs_review(memory_results: List[Dict[str, Any]]) -> str:
    needs_review = [
        r for r in memory_results
        if r.get("status") in {"needs_review", "failed"}
    ]
    if not needs_review:
        return "_No memory updates need review._\n"
    lines = []
    for r in needs_review:
        lines.append(
            f"- **{r.get('target', '?')}** {r.get('action', '?')} [{r.get('status', '?')}]: {r.get('reason', '')}"
        )
    return "\n".join(lines) + "\n"


def _section_needs_attention(
    apply_results: List[Dict[str, Any]],
    watch_results: List[Dict[str, Any]],
    memory_results: List[Dict[str, Any]],
) -> str:
    items = []
    for r in apply_results:
        if r["status"] == "failed":
            items.append(f"- **{r['skill_name']}**: apply failed - {r['reason']}")
    for r in watch_results:
        if r["status"] == "needs_review":
            items.append(f"- **{r['skill_name']}**: {r['reason']}")
    for r in memory_results:
        if r.get("status") in {"needs_review", "failed"}:
            items.append(
                f"- **memory/{r.get('target', '?')}** ({r.get('action', '?')}): {r.get('reason', '')}"
            )
    if not items:
        return "_Nothing needs your attention._\n"
    return "\n".join(items) + "\n"


def _section_operator_confidence(metrics: Optional[Dict[str, Any]]) -> str:
    if not metrics:
        return "_No operator confidence metrics yet._\n"
    window_days = int(float(metrics.get("window_days", 30)))
    patch_stability = float(metrics.get("patch_stability_ratio", 0.0))
    acc_to_reg = float(metrics.get("acceptance_to_regression_ratio", 0.0))
    mem_precision = float(metrics.get("memory_precision_proxy", 0.0))
    holdout_pass = float(metrics.get("holdout_pass_rate", 0.0))
    return (
        f"- Window: last {window_days} days\n"
        f"- Patch stability ratio: {patch_stability:.1%}\n"
        f"- Acceptance-to-regression ratio: {acc_to_reg:.2f}\n"
        f"- Memory precision proxy: {mem_precision:.1%}\n"
        f"- Holdout pass rate: {holdout_pass:.1%}\n"
    )


def generate_digest(
    apply_results: List[Dict[str, Any]],
    watch_results: List[Dict[str, Any]],
    pending_patches: List[Dict[str, Any]],
    memory_proposed: Optional[List[Dict[str, Any]]] = None,
    memory_applied: Optional[List[Dict[str, Any]]] = None,
    memory_results: Optional[List[Dict[str, Any]]] = None,
    operator_confidence: Optional[Dict[str, Any]] = None,
    report_path: Optional[Path] = None,
    report_date: Optional[str] = None,
) -> str:
    """Format and write the nightly digest."""
    date_str = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = report_path or get_default_digest_path()
    mem_proposed = memory_proposed or []
    mem_applied = memory_applied or []
    mem_results = memory_results or []

    text = f"""\
# Hermes Autoresearch - Nightly Digest {date_str}

## Applied
{_section_applied(apply_results)}
## Deferred (recency lock)
{_section_deferred(apply_results)}
## Rejected by self-play
{_section_rejected(pending_patches)}
## Regression watch
{_section_regression_watch(watch_results)}
## Proposed memory
{_section_memory_proposed(mem_proposed)}
## Applied memory
{_section_memory_applied(mem_applied)}
## Needs review
{_section_memory_needs_review(mem_results)}
## Operator confidence
{_section_operator_confidence(operator_confidence)}
## Needs your attention
{_section_needs_attention(apply_results, watch_results, mem_results)}"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return text
