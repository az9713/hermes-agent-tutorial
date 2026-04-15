"""
cron/autoresearch — Hermes autoresearch loop.

Entry points:
  run_stage1()   Extract session signals, aggregate skill health, write report.
                 Zero risk: reads state.db, writes skill_metrics.db + report.
                 Nothing in HERMES_HOME/skills/ is modified.
"""

import logging
from pathlib import Path
from typing import Optional

from cron.autoresearch.signal_extractor import extract_signals
from cron.autoresearch.skill_metrics import (
    already_extracted,
    compute_and_store_skill_health,
    get_skill_health_summary,
    open_db,
    record_session_signal,
)
from cron.autoresearch.reporter import generate_report

logger = logging.getLogger(__name__)


def run_stage1(
    state_db_path: Optional[Path] = None,
    metrics_db_path: Optional[Path] = None,
    report_path: Optional[Path] = None,
    hermes_home: Optional[Path] = None,
    since_hours: int = 24,
) -> str:
    """Run the Stage 1 autoresearch cycle.

    Steps:
      1. Extract session signals from state.db (last since_hours).
      2. Store new signals in skill_metrics.db (skip already-extracted sessions).
      3. Aggregate skill_health rows for today.
      4. Load 7-day rolling skill health summary.
      5. Generate and write nightly_report.md.

    Returns the report text.

    Args:
        state_db_path:  Path to Hermes state.db. Defaults to HERMES_HOME/state.db.
        metrics_db_path: Path to skill_metrics.db. Defaults to
                         HERMES_HOME/autoresearch/skill_metrics.db.
        report_path:    Path for nightly_report.md. Defaults to
                        HERMES_HOME/autoresearch/nightly_report.md.
        hermes_home:    Override HERMES_HOME. Used in tests.
        since_hours:    How many hours back to look for sessions. Default 24.
    """
    logger.info("Autoresearch Stage 1 starting")

    # 1. Extract signals from state.db
    signals = extract_signals(
        state_db_path=state_db_path,
        since_hours=since_hours,
        hermes_home=hermes_home,
    )
    logger.info("Extracted %d session signal(s)", len(signals))

    # 2. Store new signals (skip duplicates)
    metrics_conn = open_db(metrics_db_path)
    new_count = 0
    for signal in signals:
        if not already_extracted(metrics_conn, signal["session_id"]):
            record_session_signal(metrics_conn, signal)
            new_count += 1
    logger.info("Stored %d new signal(s) (%d already present)", new_count, len(signals) - new_count)

    # 3. Aggregate skill_health for today
    health_rows = compute_and_store_skill_health(metrics_conn)
    logger.info("Aggregated skill health for %d skill(s)", len(health_rows))

    # 4. Load 7-day rolling summary
    summary = get_skill_health_summary(metrics_conn, days=7)
    metrics_conn.close()

    # 5. Generate report
    report_text = generate_report(
        session_count=len(signals),
        skill_health=summary,
        report_path=report_path,
    )
    logger.info("Autoresearch Stage 1 complete. Report written.")
    return report_text
