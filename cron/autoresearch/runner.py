"""
cron/autoresearch/runner.py — Full-loop orchestrator for the nightly autoresearch cycle.

Public API
──────────
run_full_loop(dry_run, hermes_home, metrics_db_path, patches_path, digest_path,
              run_regression_watch, skip_stage2) → str
    Chains Stage 1 → Stage 2 → Stage 3 and returns the nightly digest text.
    Stage 2 is skipped (gracefully) if agent.auxiliary_client is unavailable.

deliver_digest(text, platforms) → dict[str, str | None]
    Sends digest text to one or more platforms (slack, telegram, …).
    Returns {platform: error_or_None} for each target.

save_run_state(state_path, status, error) → None
    Persists runtime state (last_run_at, last_status) to state.json.

load_run_state(state_path) → dict
    Reads runtime state from state.json.
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


# ── Runtime-state helpers ─────────────────────────────────────────────────────

def _default_state_path(hermes_home: Optional[Path] = None) -> Path:
    return (hermes_home or get_hermes_home()) / "autoresearch" / "state.json"


def load_run_state(state_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read autoresearch runtime state from state.json.

    Returns a dict with keys: last_run_at, last_status, last_error.
    Missing or malformed files return safe defaults (never run).
    """
    path = state_path or _default_state_path()
    if not path.exists():
        return {"last_run_at": None, "last_status": None, "last_error": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_at": None, "last_status": None, "last_error": None}


def save_run_state(
    status: str,
    error: Optional[str] = None,
    state_path: Optional[Path] = None,
) -> None:
    """Atomically write autoresearch runtime state to state.json."""
    path = state_path or _default_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "last_status": status,
        "last_error": error,
    }
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".ar_state_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Delivery ──────────────────────────────────────────────────────────────────

def deliver_digest(
    digest_text: str,
    platforms: List[str],
) -> Dict[str, Optional[str]]:
    """Send digest_text to each platform in platforms.

    For each platform, reads the home channel from the env var
    ``{PLATFORM}_HOME_CHANNEL`` (same convention as the rest of the codebase).

    Args:
        digest_text: The nightly digest markdown.
        platforms:   List of platform names, e.g. ["slack", "telegram"].

    Returns:
        Dict mapping platform name → error string (or None on success).
    """
    results: Dict[str, Optional[str]] = {}
    for platform_name in platforms:
        results[platform_name] = _deliver_to_platform(platform_name, digest_text)
    return results


def _deliver_to_platform(platform_name: str, text: str) -> Optional[str]:
    """Send text to a single platform. Returns None on success, error str on failure."""
    platform_name = platform_name.lower()
    chat_id = os.getenv(f"{platform_name.upper()}_HOME_CHANNEL", "")
    if not chat_id:
        msg = (
            f"platform '{platform_name}' has no home channel configured "
            f"(set {platform_name.upper()}_HOME_CHANNEL)"
        )
        logger.warning("Autoresearch delivery: %s", msg)
        return msg

    try:
        from gateway.config import load_gateway_config, Platform
    except ImportError as e:
        msg = f"gateway module unavailable: {e}"
        logger.error("Autoresearch delivery: %s", msg)
        return msg

    _platform_map = {
        "telegram": Platform.TELEGRAM,
        "slack": Platform.SLACK,
        "discord": Platform.DISCORD,
        "whatsapp": Platform.WHATSAPP,
        "signal": Platform.SIGNAL,
        "matrix": Platform.MATRIX,
        "mattermost": Platform.MATTERMOST,
        "homeassistant": Platform.HOMEASSISTANT,
        "dingtalk": Platform.DINGTALK,
        "feishu": Platform.FEISHU,
        "wecom": Platform.WECOM,
        "weixin": Platform.WEIXIN,
        "email": Platform.EMAIL,
        "sms": Platform.SMS,
        "bluebubbles": Platform.BLUEBUBBLES,
    }
    platform = _platform_map.get(platform_name)
    if not platform:
        msg = f"unknown platform '{platform_name}'"
        logger.warning("Autoresearch delivery: %s", msg)
        return msg

    try:
        config = load_gateway_config()
    except Exception as e:
        msg = f"failed to load gateway config: {e}"
        logger.error("Autoresearch delivery: %s", msg)
        return msg

    pconfig = config.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        msg = f"platform '{platform_name}' is not configured/enabled in gateway"
        logger.warning("Autoresearch delivery: %s", msg)
        return msg

    from tools.send_message_tool import _send_to_platform

    header = "Hermes Autoresearch — Nightly Digest\n─────────────────────────────────────\n\n"
    payload = header + text

    coro = _send_to_platform(platform, pconfig, chat_id, payload)
    try:
        result = asyncio.run(coro)
    except RuntimeError:
        coro.close()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                _send_to_platform(platform, pconfig, chat_id, payload),
            )
            result = future.result(timeout=30)
    except Exception as e:
        msg = f"send to {platform_name}:{chat_id} raised: {e}"
        logger.error("Autoresearch delivery: %s", msg)
        return msg

    if result and result.get("error"):
        msg = f"delivery error from platform: {result['error']}"
        logger.error("Autoresearch delivery: %s", msg)
        return msg

    logger.info("Autoresearch digest delivered to %s:%s", platform_name, chat_id)
    return None


# ── Full-loop orchestrator ────────────────────────────────────────────────────

def run_full_loop(
    dry_run: bool = False,
    hermes_home: Optional[Path] = None,
    metrics_db_path: Optional[Path] = None,
    patches_path: Optional[Path] = None,
    digest_path: Optional[Path] = None,
    run_regression_watch: bool = True,
    skip_stage2: bool = False,
    state_path: Optional[Path] = None,
    llm_call=None,
) -> str:
    """Run the full nightly autoresearch loop: Stage 1 → Stage 2 → Stage 3.

    Stage 2 generates LLM patch proposals. If ``agent.auxiliary_client`` is
    unavailable (e.g. no LLM configured), Stage 2 is skipped and Stage 3
    applies/watches patches from the previous run's pending_patches.json.

    Args:
        dry_run:               If True, Stage 3 writes nothing to skills/.
        hermes_home:           Override HERMES_HOME (mainly for tests).
        metrics_db_path:       Path to skill_metrics.db.
        patches_path:          Path to pending_patches.json.
        digest_path:           Path for nightly_digest.md.
        run_regression_watch:  Passed through to Stage 3.
        skip_stage2:           Force-skip Stage 2 (uses existing patches).
        state_path:            Path to state.json. Defaults to HERMES_HOME/autoresearch/state.json.

    Returns:
        The nightly digest text produced by Stage 3.
    """
    logger.info("Autoresearch full loop starting (dry_run=%s)", dry_run)
    error: Optional[str] = None

    # ── Stage 1: Observe ──────────────────────────────────────────────────────
    try:
        from cron.autoresearch import run_stage1
        run_stage1(
            metrics_db_path=metrics_db_path,
            hermes_home=hermes_home,
        )
        logger.info("Stage 1 complete")
    except Exception as e:
        logger.error("Stage 1 failed: %s", e)
        error = f"Stage 1 failed: {e}"

    # ── Stage 2: Hypothesize + Evaluate ───────────────────────────────────────
    if not skip_stage2:
        try:
            from cron.autoresearch import run_stage2
            run_stage2(
                metrics_db_path=metrics_db_path,
                patches_path=patches_path,
                hermes_home=hermes_home,
                llm_call=llm_call,
            )
            logger.info("Stage 2 complete")
        except ImportError as e:
            # agent.auxiliary_client unavailable — skip gracefully
            logger.warning(
                "Stage 2 skipped: LLM client unavailable (%s). "
                "Stage 3 will use patches from the previous run.",
                e,
            )
        except Exception as e:
            logger.error("Stage 2 failed: %s", e)
            if error is None:
                error = f"Stage 2 failed: {e}"

    # ── Stage 3: Apply + Recover ───────────────────────────────────────────────
    digest_text: str = ""
    try:
        from cron.autoresearch import run_stage3
        digest_text = run_stage3(
            metrics_db_path=metrics_db_path,
            patches_path=patches_path,
            digest_path=digest_path,
            hermes_home=hermes_home,
            dry_run=dry_run,
            run_regression_watch=run_regression_watch,
        )
        logger.info("Stage 3 complete")
    except Exception as e:
        logger.error("Stage 3 failed: %s", e)
        digest_text = f"# Hermes Autoresearch — Error\n\nStage 3 failed: {e}\n"
        if error is None:
            error = f"Stage 3 failed: {e}"

    # ── Persist run state ────────────────────────────────────────────────────
    save_run_state(
        status="error" if error else "ok",
        error=error,
        state_path=state_path,
    )

    logger.info("Autoresearch full loop complete")
    return digest_text
