#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Provides bounded, file-backed memory that persists across sessions. Two stores:
  - MEMORY.md: agent's personal notes and observations (environment facts, project
    conventions, tool quirks, things learned)
  - USER.md: what the agent knows about the user (preferences, communication style,
    expectations, workflow habits)

Both are injected into the system prompt as a frozen snapshot at session start.
Mid-session writes update files on disk immediately (durable). For the base snapshot,
the content is frozen to preserve the Anthropic prefix cache. However, entries added
*during* the current session are appended in a separate "Added this session" block so
the LLM can see them immediately without waiting for a full session restart.

Entry delimiter: § (section sign). Entries can be multiline.
Character limits (not tokens) because char counts are model-independent.

Priority tiers:
  - high:      [HIGH] prefix — sorted first, never auto-evicted on char overflow.
  - normal:    no prefix — backward compatible with existing MEMORY.md content.
  - ephemeral: [EPHEMERAL expires=YYYY-MM-DD] prefix — auto-dropped after expiry date.

Design:
- Single `memory` tool with action parameter: add, replace, remove, read
- replace/remove use short unique substring matching (not full text or IDs)
- Behavioral guidance lives in the tool schema description
- Frozen snapshot pattern: system prompt base is stable for cache; session additions
  appended live
"""

import json
import logging
import os
import re
import string
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, Any, List, Optional, Tuple

# fcntl is Unix-only. Hermes does not officially support Windows (requires WSL2),
# but we guard the import so the module can be imported on Windows for testing.
if sys.platform != "win32":
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Where memory files live — resolved dynamically so profile overrides
# (HERMES_HOME env var changes) are always respected.  The old module-level
# constant was cached at import time and could go stale if a profile switch
# happened after the first import.
def get_memory_dir() -> Path:
    """Return the profile-scoped memories directory."""
    return get_hermes_home() / "memories"

# Backward-compatible alias — gateway/run.py imports this at runtime inside
# a function body, so it gets the correct snapshot for that process.  New code
# should prefer get_memory_dir().
MEMORY_DIR = get_memory_dir()

ENTRY_DELIMITER = "\n§\n"

# Valid priority values for the add() action.
VALID_PRIORITIES = ("high", "normal", "ephemeral")
_DEFAULT_EPHEMERAL_DAYS = 30

# Stopwords stripped before overlap computation (contradiction detection).
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "of", "to", "in", "for",
    "and", "or", "i", "my", "me", "user", "be", "it", "this", "that",
    "with", "on", "at", "by", "from", "as", "do", "not", "no",
})


# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# in content that gets injected into the system prompt.
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    # Prompt injection
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget with secrets
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence via shell rc
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/\.ssh', "ssh_access"),
    (r'\$HOME/\.hermes/\.env|\~/\.hermes/\.env', "hermes_env"),
]

# Subset of invisible chars for injection detection
_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def _scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    # Check invisible unicode
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    # Check threat patterns
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: content matches threat pattern '{pid}'. Memory entries are injected into the system prompt and must not contain injection or exfiltration payloads."

    return None


# ---------------------------------------------------------------------------
# Priority / expiry helpers
# ---------------------------------------------------------------------------

def _today() -> date:
    """Return today's date. Isolated so tests can monkeypatch it."""
    return date.today()


def _make_ephemeral_prefix(expires: date) -> str:
    return f"[EPHEMERAL expires={expires.isoformat()}]"


def _parse_entry_priority(entry: str) -> Tuple[str, str]:
    """
    Parse the priority prefix from a raw stored entry.

    Returns (priority, bare_content) where priority is one of
    "high", "ephemeral", "normal".  bare_content strips the prefix.
    """
    if entry.startswith("[HIGH] "):
        return "high", entry[7:]
    m = re.match(r'^\[EPHEMERAL expires=(\d{4}-\d{2}-\d{2})\] ', entry)
    if m:
        return "ephemeral", entry[m.end():]
    return "normal", entry


def _is_expired(entry: str) -> bool:
    """Return True if entry is ephemeral and its expiry date has passed."""
    m = re.match(r'^\[EPHEMERAL expires=(\d{4}-\d{2}-\d{2})\] ', entry)
    if not m:
        return False
    try:
        expiry = date.fromisoformat(m.group(1))
    except ValueError:
        return False
    return _today() > expiry


def _sort_entries_by_priority(entries: List[str]) -> List[str]:
    """Sort entries so [HIGH] comes first, then normal, then ephemeral."""
    high = [e for e in entries if e.startswith("[HIGH] ")]
    normal = [e for e in entries if not e.startswith("[HIGH] ") and not re.match(r'^\[EPHEMERAL', e)]
    ephemeral = [e for e in entries if re.match(r'^\[EPHEMERAL', e)]
    return high + normal + ephemeral


# ---------------------------------------------------------------------------
# Contradiction / overlap detection
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> frozenset:
    """Lowercase words minus stopwords and punctuation."""
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return frozenset(w for w in text.split() if w and w not in _STOPWORDS)


def _detect_overlap(new_content: str, existing_entries: List[str]) -> List[str]:
    """
    Return list of existing entries that have >40% token overlap with new_content.

    Overlap = |intersection| / max(len(new_tokens), 1).
    Entries that are exact duplicates are excluded (handled by dedup elsewhere).
    """
    new_tokens = _tokenize(new_content)
    if not new_tokens:
        return []

    overlapping = []
    for entry in existing_entries:
        if entry == new_content:
            continue  # exact dup handled separately
        _, bare = _parse_entry_priority(entry)
        entry_tokens = _tokenize(bare)
        overlap = len(new_tokens & entry_tokens) / max(len(new_tokens), 1)
        if overlap > 0.4:
            overlapping.append(entry)
    return overlapping


class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for the *base* of
        system prompt injection. Never mutated mid-session. Keeps prefix cache stable.
      - _session_additions: entries added *during this session* — appended to the
        system prompt output so the LLM can see them immediately.
      - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Frozen base snapshot for system prompt — set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}
        # Entries added during the current session (not in the frozen snapshot)
        self._session_additions: Dict[str, List[str]] = {"memory": [], "user": []}

    def load_from_disk(self):
        """Load entries from MEMORY.md and USER.md, capture system prompt snapshot."""
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # Drop expired ephemeral entries on load; persist if any were removed
        for target in ("memory", "user"):
            entries = self._entries_for(target)
            filtered = [e for e in entries if not _is_expired(e)]
            if len(filtered) < len(entries):
                self._set_entries(target, filtered)
                self.save_to_disk(target)

        # Deduplicate entries (preserves order, keeps first occurrence)
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # Capture frozen snapshot for system prompt injection
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }
        # Clear per-session additions on any reload
        self._session_additions = {"memory": [], "user": []}

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety.

        Uses a separate .lock file so the memory file itself can still be
        atomically replaced via os.replace().

        On Windows (fcntl unavailable), falls back to a no-op context manager —
        acceptable because Hermes officially requires Unix (WSL2 on Windows).
        """
        if fcntl is None:
            # No-op on Windows — Hermes is not officially supported there
            yield
            return

        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str):
        """Re-read entries from disk into in-memory state.

        Called under file lock to get the latest state before mutating.
        Also drops expired ephemeral entries on reload.
        """
        fresh = self._read_file(self._path_for(target))
        fresh = list(dict.fromkeys(fresh))  # deduplicate
        fresh = [e for e in fresh if not _is_expired(e)]
        self._set_entries(target, fresh)

    def save_to_disk(self, target: str):
        """Persist entries to the appropriate file. Called after every mutation."""
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    def _entries_for(self, target: str) -> List[str]:
        if target == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]):
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        return self.memory_char_limit

    def add(
        self,
        target: str,
        content: str,
        priority: str = "normal",
        expires_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Append a new entry. Returns error if it would exceed the char limit.

        priority: "high", "normal" (default), or "ephemeral".
        expires_days: only used when priority="ephemeral". Defaults to 30.
        """
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        if priority not in VALID_PRIORITIES:
            return {
                "success": False,
                "error": f"Invalid priority '{priority}'. Use: {', '.join(VALID_PRIORITIES)}.",
            }

        # Build the stored entry with prefix
        if priority == "high":
            stored_entry = f"[HIGH] {content}"
        elif priority == "ephemeral":
            days = expires_days if expires_days is not None else _DEFAULT_EPHEMERAL_DAYS
            expires = _today() + timedelta(days=days)
            stored_entry = f"{_make_ephemeral_prefix(expires)} {content}"
        else:
            stored_entry = content

        # Scan for injection/exfiltration before accepting (scan bare content)
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            # Re-read from disk under lock to pick up writes from other sessions
            self._reload_target(target)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            # Reject exact duplicates
            if stored_entry in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            # Contradiction detection — warn but don't block
            overlapping = _detect_overlap(content, entries)

            # Attempt to fit within limit; evict ephemeral then normal if needed
            new_entries = entries + [stored_entry]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            if new_total > limit:
                # Try evicting ephemeral entries first
                evictable = [e for e in entries if re.match(r'^\[EPHEMERAL', e)]
                if evictable:
                    for evict in evictable:
                        entries.remove(evict)
                        new_entries = entries + [stored_entry]
                        new_total = len(ENTRY_DELIMITER.join(new_entries))
                        if new_total <= limit:
                            break

            if new_total > limit:
                # Try evicting oldest normal entries
                normal_entries = [
                    e for e in entries
                    if not e.startswith("[HIGH] ") and not re.match(r'^\[EPHEMERAL', e)
                ]
                for evict in normal_entries:
                    entries.remove(evict)
                    new_entries = entries + [stored_entry]
                    new_total = len(ENTRY_DELIMITER.join(new_entries))
                    if new_total <= limit:
                        break

            if new_total > limit:
                current = len(ENTRY_DELIMITER.join(entries))
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(stored_entry)} chars) would exceed the limit "
                        f"even after evicting ephemeral and normal entries. "
                        f"Consolidate or remove existing high-priority entries first."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(stored_entry)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        # Track this addition for intra-session visibility
        self._session_additions[target].append(stored_entry)

        result = self._success_response(target, "Entry added.")
        if overlapping:
            previews = [e[:80] + ("..." if len(e) > 80 else "") for e in overlapping]
            result["warning"] = (
                f"This may overlap with {len(overlapping)} existing "
                f"entr{'y' if len(overlapping) == 1 else 'ies'}: "
                + "; ".join(f"'{p}'" for p in previews)
                + " — consider editing or removing the old one."
            )
        return result

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        # Scan replacement content for injection/exfiltration
        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), operate on the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to replace just the first

            idx = matches[0][0]
            limit = self._char_limit(target)

            # Check that replacement doesn't blow the budget
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), remove the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to remove just the first

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry removed.")

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """
        Return memory content for system prompt injection.

        Returns the frozen base snapshot (captured at load_from_disk() time) plus
        any entries added during the current session in a separate block.

        The base snapshot is frozen to preserve the Anthropic prefix cache —
        its content is stable across turns until the next session start.
        Session additions cause one cache miss each but re-stabilize immediately
        after.

        Returns None if there is no content to show.
        """
        base = self._system_prompt_snapshot.get(target, "")
        additions = self._session_additions.get(target, [])

        if not base and not additions:
            return None

        if not additions:
            return base if base else None

        # Render the additions block
        sorted_additions = _sort_entries_by_priority(additions)
        additions_text = ENTRY_DELIMITER.join(sorted_additions)
        additions_block = f"## Added this session\n{additions_text}"

        if base:
            return f"{base}\n\n{additions_block}"
        return additions_block

    # -- Internal helpers --

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        # Sort by priority for display
        sorted_entries = _sort_entries_by_priority(entries)

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(sorted_entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a memory file and split into entries.

        No file locking needed: _write_file uses atomic rename, so readers
        always see either the previous complete file or the new complete file.
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # Use ENTRY_DELIMITER for consistency with _write_file. Splitting by "§"
        # alone would incorrectly split entries that contain "§" in their content.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """Write entries to a memory file using atomic temp-file + rename.

        Previous implementation used open("w") + flock, but "w" truncates the
        file *before* the lock is acquired, creating a race window where
        concurrent readers see an empty file. Atomic rename avoids this:
        readers always see either the old complete file or the new one.
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            # Write to temp file in same directory (same filesystem for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(path))  # Atomic on same filesystem
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


def memory_tool(
    action: str,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    priority: str = "normal",
    expires_days: Optional[int] = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """
    Single entry point for the memory tool. Dispatches to MemoryStore methods.

    Returns JSON string with results.
    """
    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    if target not in ("memory", "user"):
        return tool_error(f"Invalid target '{target}'. Use 'memory' or 'user'.", success=False)

    if action == "add":
        if not content:
            return tool_error("Content is required for 'add' action.", success=False)
        result = store.add(target, content, priority=priority, expires_days=expires_days)

    elif action == "replace":
        if not old_text:
            return tool_error("old_text is required for 'replace' action.", success=False)
        if not content:
            return tool_error("content is required for 'replace' action.", success=False)
        result = store.replace(target, old_text, content)

    elif action == "remove":
        if not old_text:
            return tool_error("old_text is required for 'remove' action.", success=False)
        result = store.remove(target, old_text)

    else:
        return tool_error(f"Unknown action '{action}'. Use: add, replace, remove", success=False)

    return json.dumps(result, ensure_ascii=False)


def check_memory_requirements() -> bool:
    """Memory tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save durable information to persistent memory that survives across sessions. "
        "Memory is injected into future turns, so keep it compact and focused on facts "
        "that will still matter later.\n\n"
        "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
        "- User corrects you or says 'remember this' / 'don't do that again'\n"
        "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
        "- You discover something about the environment (OS, installed tools, project structure)\n"
        "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
        "- You identify a stable fact that will be useful again in future sessions\n\n"
        "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
        "The most valuable memory prevents the user from having to repeat themselves.\n\n"
        "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
        "state to memory; use session_search to recall those from past transcripts.\n"
        "If you've discovered a new way to do something, solved a problem that could be "
        "necessary later, save it as a skill with the skill tool.\n\n"
        "TWO TARGETS:\n"
        "- 'user': who the user is -- name, role, preferences, communication style, pet peeves\n"
        "- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned\n\n"
        "ACTIONS: add (new entry), replace (update existing -- old_text identifies it), "
        "remove (delete -- old_text identifies it).\n\n"
        "PRIORITY TIERS (optional, default 'normal'):\n"
        "- 'high': critical facts; sorted first in prompt, never auto-evicted on overflow.\n"
        "- 'normal': standard entries (default).\n"
        "- 'ephemeral': time-limited entries; auto-expire after expires_days (default 30).\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for user profile."
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'."
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove."
            },
            "priority": {
                "type": "string",
                "enum": ["high", "normal", "ephemeral"],
                "description": (
                    "Entry importance tier (default: 'normal'). "
                    "'high' entries are never auto-evicted and sort first. "
                    "'ephemeral' entries expire after expires_days days."
                )
            },
            "expires_days": {
                "type": "integer",
                "description": (
                    "Days until an 'ephemeral' entry expires (default: 30). "
                    "Only used when priority='ephemeral'."
                )
            },
        },
        "required": ["action", "target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        priority=args.get("priority", "normal"),
        expires_days=args.get("expires_days"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)
