"""
digest.py — Format a human-readable nightly digest for Stage 3.

The digest summarises what happened across the full nightly cycle:
  - Patches applied
  - Patches deferred (recency lock)
  - Patches rejected by self-play
  - Regression watch outcomes
  - Items needing human attention

It is written to ~/.hermes/autoresearch/nightly_digest.md and returned as
a string. Delivery to external platforms (Slack, Telegram, etc.) is out of
scope for Stage 3 — the plan notes this is opt-in via config.

Public API:
  generate_digest(apply_results, watch_results, pending_patches, report_path)
    → str
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home


def get_default_digest_path() -> Path:
    return get_hermes_home() / "autoresearch" / "nightly_digest.md"


# ── Section builders ──────────────────────────────────────────────────────────

def _section_applied(apply_results: List[Dict[str, Any]]) -> str:
    applied = [r for r in apply_results if r["status"] == "applied"]
    if not applied:
        return "_No patches applied this cycle._\n"
    lines = []
    for r in applied:
        lines.append(f"- **{r['skill_name']}**: {r['reason']}")
    return "\n".join(lines) + "\n"


def _section_deferred(apply_results: List[Dict[str, Any]]) -> str:
    deferred = [r for r in apply_results if r["status"] == "deferred"]
    if not deferred:
        return "_No patches deferred._\n"
    lines = []
    for r in deferred:
        lines.append(f"- **{r['skill_name']}**: {r['reason']}")
    return "\n".join(lines) + "\n"


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
            f"quality_delta={qd:+.1f} — {p.get('rejection_reason', '')}"
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


def _section_needs_attention(
    apply_results: List[Dict[str, Any]],
    watch_results: List[Dict[str, Any]],
) -> str:
    items = []
    for r in apply_results:
        if r["status"] == "failed":
            items.append(f"- **{r['skill_name']}**: apply failed — {r['reason']}")
    for r in watch_results:
        if r["status"] == "needs_review":
            items.append(f"- **{r['skill_name']}**: {r['reason']}")
    if not items:
        return "_Nothing needs your attention._\n"
    return "\n".join(items) + "\n"


# ── Public API ────────────────────────────────────────────────────────────────

def generate_digest(
    apply_results: List[Dict[str, Any]],
    watch_results: List[Dict[str, Any]],
    pending_patches: List[Dict[str, Any]],
    report_path: Optional[Path] = None,
    report_date: Optional[str] = None,
) -> str:
    """Format and write the nightly digest.

    Args:
        apply_results:   List of ApplyResult dicts from apply_patches().
        watch_results:   List of WatchResult dicts from check_regressions().
        pending_patches: Full list from read_pending_patches() — used for the
                         "rejected by self-play" section.
        report_path:     Output path. Defaults to HERMES_HOME/autoresearch/nightly_digest.md.
        report_date:     Date string for the header (YYYY-MM-DD). Defaults to today UTC.

    Returns:
        The digest text (also written to report_path).
    """
    date_str = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = report_path or get_default_digest_path()

    text = f"""\
# Hermes Autoresearch — Nightly Digest {date_str}

## Applied
{_section_applied(apply_results)}
## Deferred (recency lock)
{_section_deferred(apply_results)}
## Rejected by self-play
{_section_rejected(pending_patches)}
## Regression watch
{_section_regression_watch(watch_results)}
## Needs your attention
{_section_needs_attention(apply_results, watch_results)}"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return text
