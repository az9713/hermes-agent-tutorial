"""
reporter.py — Generate the nightly autoresearch Markdown report.

Produces a human-readable summary at ~/.hermes/autoresearch/nightly_report.md
covering:
  - Session stats for the last 24h
  - Per-skill health table (from skill_health aggregates)
  - Flagged skills (above underperformance thresholds)
  - Missing coverage: task types appearing frequently with no skill

Stage 1 produces this report only — no patches are applied.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

# Thresholds that define an underperforming skill.
CORRECTION_RATE_THRESHOLD = 0.30   # flag if > 30 % of sessions have corrections
COMPLETION_RATE_THRESHOLD = 0.50   # flag if < 50 % of sessions complete naturally
TOKEN_EFFICIENCY_FLAG = 2_000      # flag avg tokens if above this (rough proxy)


def get_default_report_path() -> Path:
    return get_hermes_home() / "autoresearch" / "nightly_report.md"


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def _format_tokens(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def _skill_status(health: Dict[str, Any]) -> str:
    """Return 'FLAGGED' or 'OK' based on health metrics."""
    cr = health.get("correction_rate") or 0
    compr = health.get("completion_rate") or 0
    if cr > CORRECTION_RATE_THRESHOLD or compr < COMPLETION_RATE_THRESHOLD:
        return "FLAGGED ⚠"
    return "OK ✓"


def generate_report(
    session_count: int,
    skill_health: List[Dict[str, Any]],
    report_path: Optional[Path] = None,
    report_date: Optional[str] = None,
) -> str:
    """Build and write the nightly report. Returns the report text.

    Args:
        session_count:  Number of sessions analysed in the last 24h.
        skill_health:   List of skill health dicts from skill_metrics.get_skill_health_summary().
        report_path:    Where to write the report. Defaults to ~/.hermes/autoresearch/nightly_report.md.
        report_date:    Date string for the report header (YYYY-MM-DD). Defaults to today UTC.
    """
    path = report_path or get_default_report_path()
    date_str = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines: List[str] = []

    # Header
    lines += [
        f"# Hermes Autoresearch — Nightly Report {date_str}",
        "",
        "> Stage 1: Observe only. No patches applied.",
        "",
    ]

    # Session summary
    lines += [
        "## Sessions Analysed",
        "",
        f"- **{session_count}** session(s) from the last 24h",
        f"- **{len(skill_health)}** skill(s) with recorded invocations in the last 7 days",
        "",
    ]

    # Skill health table
    if skill_health:
        lines += [
            "## Skill Health (7-day rolling)",
            "",
            "| Skill | Invocations | Avg Tokens | Correction Rate | Completion Rate | Status |",
            "|-------|-------------|------------|-----------------|-----------------|--------|",
        ]
        for h in skill_health:
            status = _skill_status(h)
            lines.append(
                f"| {h['skill_name']} "
                f"| {h['total_invocations'] or 0} "
                f"| {_format_tokens(h.get('avg_tokens'))} "
                f"| {_format_pct(h.get('correction_rate'))} "
                f"| {_format_pct(h.get('completion_rate'))} "
                f"| {status} |"
            )
        lines.append("")
    else:
        lines += [
            "## Skill Health",
            "",
            "_No skill invocations recorded in the last 7 days._",
            "",
        ]

    # Flagged skills detail
    flagged = [h for h in skill_health if _skill_status(h) == "FLAGGED ⚠"]
    if flagged:
        lines += ["## Flagged Skills", ""]
        for h in flagged:
            reasons = []
            cr = h.get("correction_rate") or 0
            compr = h.get("completion_rate") or 0
            if cr > CORRECTION_RATE_THRESHOLD:
                reasons.append(
                    f"correction_rate {_format_pct(cr)} > threshold {_format_pct(CORRECTION_RATE_THRESHOLD)}"
                )
            if compr < COMPLETION_RATE_THRESHOLD:
                reasons.append(
                    f"completion_rate {_format_pct(compr)} < threshold {_format_pct(COMPLETION_RATE_THRESHOLD)}"
                )
            lines.append(f"- **{h['skill_name']}**: {'; '.join(reasons)}")
        lines.append("")
        lines += [
            "> Stage 2 will generate candidate patches for these skills.",
            "",
        ]
    else:
        lines += [
            "## Flagged Skills",
            "",
            "_No skills flagged this cycle._",
            "",
        ]

    # Stage note
    lines += [
        "---",
        "",
        "_Autoresearch Stage 1 (Observe). "
        "Patches will be proposed in Stage 2 and applied in Stage 3._",
    ]

    report_text = "\n".join(lines) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text, encoding="utf-8")

    return report_text
