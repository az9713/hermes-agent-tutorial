# Expiry Edge-Case Tests — Evidence, Interpretation, and Limits

> This document is the complete reference for the `TestExpiryEdgeCases` class in
> `tests/tools/test_memory_tool.py`.
> It answers four questions: *why these tests exist*, *what passed*, *what that proves*,
> and *what it does not prove*.

---

## 1. Why These Tests Exist

### The gap they close

The ephemeral memory tier was introduced as part of the 7-enhancement plan. An entry with
`priority="ephemeral"` gets a `[EPHEMERAL expires=YYYY-MM-DD]` prefix. On every
`load_from_disk()` call and every `add()` call, expired entries are pruned and the file
is re-written without them.

The pruning logic lives in a single boolean at `tools/memory_tool.py:164`:

```python
def _is_expired(entry: str) -> bool:
    """Return True if entry is ephemeral and its expiry date has passed."""
    m = re.match(r'^\[EPHEMERAL expires=(\d{4}-\d{2}-\d{2})\] ', entry)
    if not m:
        return False
    try:
        expiry = date.fromisoformat(m.group(1))
    except ValueError:
        return False
    return _today() > expiry          # ← strict greater-than, not >=
```

The operator `>` (not `>=`) is a deliberate design choice: an entry whose expiry date is
**today** is kept, not dropped. The entry is only dropped starting **the day after** the
expiry date.

Before `TestExpiryEdgeCases` was written, the pre-existing tests for this logic were:

```python
# In TestPriorityHelpers — lines 422-428
def test_is_expired_past_date(self):
    past = date.today() - timedelta(days=1)
    assert _is_expired(f"[EPHEMERAL expires={past.isoformat()}] entry") is True

def test_is_expired_future_date(self):
    future = date.today() + timedelta(days=10)
    assert _is_expired(f"[EPHEMERAL expires={future.isoformat()}] entry") is False
```

These two tests have two problems:

**Problem 1 — The boundary is not tested.** `timedelta(days=1)` means "yesterday" and
`timedelta(days=10)` means "well into the future". Neither test asks: *what happens when
expiry == today?* That is the only day where `>` and `>=` produce different results. The
operator could be changed to `>=` without either test failing. The boundary — the exact
design contract — is invisible.

**Problem 2 — Clock dependency.** Both tests call `date.today()` at test execution time.
If a test starts just before midnight (11:59:59 PM) and the assertion runs just after
(12:00:00 AM the next day), the expiry date `past = date.today() - timedelta(days=1)`
was computed for the previous "today" but the comparison `_today() > expiry` now uses
the new "today". In practice this risk is negligible (the test runs in microseconds), but
the tests are technically non-deterministic across the midnight boundary.

`TestExpiryEdgeCases` addresses both problems: it pins `_today()` to a fixed date and
uses exact boundary dates (`expiry_date - 1 day`, `expiry_date`, `expiry_date + 1 day`).

### Why `_today()` was isolated in the first place

`_today()` at `tools/memory_tool.py:131-133` is a four-line wrapper:

```python
def _today() -> date:
    """Return today's date. Isolated so tests can monkeypatch it."""
    return date.today()
```

The docstring records the reason: *"Isolated so tests can monkeypatch it."* The function
was written with this test scenario in mind. No refactoring was required to make these
tests possible — the production code already anticipated the need.

### Gap provenance

The gap was first documented in `docs/analysis/implementation-status.md` Part 5:

> **3. Add `_today()` monkeypatch tests for expiry edge cases.**
> The `_today()` function is already isolated for this reason. Add a test where two
> entries — one expiring today, one expiring tomorrow — are loaded after monkeypatching
> `_today` to today. Verify exactly one is dropped.

---

## 2. Test Receipt — Verbatim Output

Run command:

```
python -m pytest tests/tools/test_memory_tool.py::TestExpiryEdgeCases -v --override-ini="addopts="
```

Environment: Python 3.13.5 · pytest 8.4.2 · platform win32 · 2026-04-11

```
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-8.4.2, pluggy-1.6.0 -- C:\Users\simon\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent
configfile: pyproject.toml
plugins: anyio-4.9.0, langsmith-0.3.45, asyncio-1.3.0, cov-7.0.0, mock-3.15.1, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 5 items

tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_today_is_not_expired PASSED [ 20%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_yesterday_is_expired PASSED [ 40%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_tomorrow_is_not_expired PASSED [ 60%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_load_from_disk_drops_exactly_expired_entries PASSED [ 80%]
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_reload_target_drops_exactly_expired_entries PASSED [100%]

============================== warnings summary ===============================
tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_entry_expiring_today_is_not_expired
  C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent\tests\conftest.py:91: DeprecationWarning: There is no current event loop
    loop = asyncio.get_event_loop_policy().get_event_loop()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 5 passed, 1 warning in 3.22s =========================
```

**Note on the warning.** The single `DeprecationWarning` comes from `tests/conftest.py:91`
— a shared fixture that calls `asyncio.get_event_loop_policy().get_event_loop()`. It fires
on the first test collected in any run, regardless of which test module runs. It is a
pre-existing fixture issue in the upstream test infrastructure, unrelated to anything in
`TestExpiryEdgeCases`. No test in this class uses asyncio.

**Note on `--override-ini="addopts="`.**  `pyproject.toml` sets
`addopts = "-m 'not integration' -n auto"`. The `-n auto` flag requires `pytest-xdist`,
which is not installed in this Windows environment. Clearing `addopts` runs tests
sequentially. On a full Linux dev environment with all dependencies, the run command is
simply `python -m pytest tests/tools/test_memory_tool.py::TestExpiryEdgeCases -v`.

**Summary: 5 passed, 0 failed, 0 errors, 1 pre-existing warning. Run time: 3.22 s.**

---

## 3. Test Strategy and Architecture

### The two layers under test

The 5 tests cover two distinct layers of the expiry system.

```
Layer 1: _is_expired(entry) → bool           (tools/memory_tool.py:155)
│
│  Takes a raw stored string. Parses the [EPHEMERAL expires=...] prefix
│  via regex. Calls _today(). Returns True if _today() > expiry.
│  No file I/O. No MemoryStore state.
│
Tests 1, 2, 3 target this layer directly.

Layer 2: Expiry pruning in MemoryStore        (tools/memory_tool.py:231, 292)
│
│  load_from_disk() — lines 239-245:
│    for target in ("memory", "user"):
│        entries = self._entries_for(target)
│        filtered = [e for e in entries if not _is_expired(e)]
│        if len(filtered) < len(entries):
│            self._set_entries(target, filtered)
│            self.save_to_disk(target)
│
│  _reload_target() — lines 298-301:
│    fresh = self._read_file(self._path_for(target))
│    fresh = list(dict.fromkeys(fresh))     # deduplicate
│    fresh = [e for e in fresh if not _is_expired(e)]
│    self._set_entries(target, fresh)
│
Tests 4 and 5 target this layer — full MemoryStore with real files.
```

The two layers are tested independently so a failure localises precisely. If tests 1-3
pass but test 4 fails, the bug is in the `load_from_disk` loop, not in `_is_expired`
itself. If test 4 passes but test 5 fails, the bug is in the `_reload_target` path
specifically.

### The `pin_today` fixture

All five tests use a module-level fixture defined at line 447:

```python
FIXED_TODAY = date(2026, 4, 15)

@pytest.fixture
def pin_today(monkeypatch):
    """Pin tools.memory_tool._today to FIXED_TODAY for deterministic expiry tests."""
    monkeypatch.setattr("tools.memory_tool._today", lambda: FIXED_TODAY)
```

**How the monkeypatch works.** `_today` is a module-level function in `tools.memory_tool`.
`monkeypatch.setattr("tools.memory_tool._today", lambda: FIXED_TODAY)` replaces the name
`_today` in the `tools.memory_tool` module's namespace for the duration of the test, then
restores the original after the test exits. Every downstream caller — `_is_expired`,
`load_from_disk`, `_reload_target`, `add` — calls `_today()` by resolving the name from
the same module namespace, so they all see the pinned date.

**Why a module-level patch, not a class attribute.** If `_today()` were a method on
`MemoryStore`, the patch target would be `tools.memory_tool.MemoryStore._today`. Because
it is a standalone module-level function, the patch target is just the module path. This
is simpler and also means the patch works even for code paths that call `_today()` without
a `MemoryStore` instance (e.g., the `_is_expired` helper, which is also called from
outside the class by tests and hypothetically by future callers).

**Why 2026-04-15?** It is an arbitrary fixed date with the useful property that
`2026-04-14` (yesterday), `2026-04-15` (today), and `2026-04-16` (tomorrow) are easy to
read and verify at a glance. The date is in the future relative to when the code was
written (2026-04-11), which means it will never accidentally match `date.today()` during
normal development — an accidental match would mean tests 1 and 2 would behave
differently depending on whether the real clock agreed with the pinned date.

### Infrastructure for tests 4 and 5: redirecting file I/O

Tests 4 and 5 need a real `MemoryStore` reading and writing real files, but they must not
touch `~/.hermes/memories/`. The isolation requires three monkeypatches applied in
addition to `pin_today`:

```python
monkeypatch.setattr("tools.memory_tool.MEMORY_DIR", tmp_path)
monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
```

**Why both `MEMORY_DIR` and `get_memory_dir`?**

`MEMORY_DIR` is a module-level constant set at import time:
```python
MEMORY_DIR = get_memory_dir()          # computed once, at import
```

It is a backward-compatibility alias used by legacy call sites (e.g., `gateway/run.py`).
Patching only `MEMORY_DIR` would redirect any code that reads the constant directly, but
`MemoryStore` methods call `get_memory_dir()` **dynamically** — for example:

```python
def load_from_disk(self):
    mem_dir = get_memory_dir()         # resolves fresh on every call
    ...

def _path_for(target: str) -> Path:
    mem_dir = get_memory_dir()
    ...

def save_to_disk(self, target: str):
    get_memory_dir().mkdir(parents=True, exist_ok=True)
    ...
```

Patching only `get_memory_dir` would redirect `MemoryStore` but leave `MEMORY_DIR` stale.
Both are patched to prevent any code path from escaping to the real home directory.

**Why not use the `_skill_dir` pattern from the skill manager tests?**

`_skill_dir` in `tests/tools/test_skill_manager_tool.py:32` is a context manager that
patches two constants. An equivalent `_memory_dir` context manager could be created, but
the two-line explicit `monkeypatch.setattr` approach was chosen for clarity — the test is
self-contained without importing a helper, and the pattern is already established in
`test_ephemeral_expires_on_reload` (line 302), which the tests are designed to supersede.

**`MemoryStore` constructor: keyword arguments required.**

`MemoryStore.__init__` signature:
```python
def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
```

The first positional argument is `memory_char_limit`, an integer. Tests 4 and 5 use:
```python
s = MemoryStore(memory_char_limit=2200, user_char_limit=1375)
```

This is the same pattern as `test_ephemeral_expires_on_reload` (line 306). Using explicit
keyword arguments prevents the bug documented in the Development History section below.

### Development history and the bug that was fixed

The initial implementation of tests 4 and 5 passed `tmp_path` as the first positional
argument:

```python
# WRONG — initial implementation
s = MemoryStore(tmp_path)
```

`tmp_path` is a `WindowsPath` object. Passing it as the first positional argument set
`memory_char_limit = WindowsPath(...)`. This was not rejected at construction time because
`__init__` does not validate the type of `memory_char_limit`. The error surfaced only when
`add()` was called, at line 386:

```python
if new_total > limit:
    ^^^^^^^^^^^^^^^^^^
TypeError: '>' not supported between instances of 'int' and 'WindowsPath'
```

The error message pointed to `memory_tool.py:386`, deep inside `add()`, with no hint that
the constructor had been called incorrectly.

The initial implementation also omitted the `MEMORY_DIR` and `get_memory_dir` patches.
Without them, `load_from_disk()` called `get_memory_dir()` which returned the real
`~/.hermes/memories/` directory. The test wrote entries to `tmp_path / "MEMORY.md"` but
the store loaded from a different directory, so it found no entries. Both tests 4 and 5
reported `assert 0 == 2` — zero surviving entries instead of two.

The verbatim failure output before the fix:

```
FAILED tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_load_from_disk_drops_exactly_expired_entries
E       assert 0 == 2
E        +  where 0 = len([])

FAILED tests/tools/test_memory_tool.py::TestExpiryEdgeCases::test_reload_target_drops_exactly_expired_entries
TypeError: '>' not supported between instances of 'int' and 'WindowsPath'
```

Fix applied:
1. Changed `MemoryStore(tmp_path)` → `MemoryStore(memory_char_limit=2200, user_char_limit=1375)`
2. Added `monkeypatch.setattr("tools.memory_tool.MEMORY_DIR", tmp_path)`
3. Added `monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)`
4. Added `monkeypatch` as an explicit parameter to both test methods (alongside `pin_today`) so pytest injects the same `monkeypatch` instance to both the fixture and the test function

After the fix: 5 passed, 0 failed.

---

## 4. Test-by-Test Interpretation

### Test 1 — `test_entry_expiring_today_is_not_expired` PASSED

```python
def test_entry_expiring_today_is_not_expired(self, pin_today):
    # today == expiry: _today() > expiry is False → entry is KEPT.
    # This pins the > (not >=) contract. If the operator were changed to >=,
    # this test would fail, making the regression immediately visible.
    entry = "[EPHEMERAL expires=2026-04-15] expiring today"
    assert _is_expired(entry) is False
```

**What it tests.** `_today()` returns `date(2026, 4, 15)`. The entry's expiry date is
`date(2026, 4, 15)`. The comparison is `date(2026, 4, 15) > date(2026, 4, 15)` which is
`False`. So `_is_expired` returns `False`.

**What PASSED proves.** The `>` operator is correctly strict. An entry set to expire
"today" is **kept** for the entirety of today. It will only be dropped tomorrow. This is
the intended user-facing behaviour: if you add an ephemeral note with
`expires_days=1` (default expiry 30 days from today), it will be available for the full
day on its expiry date and gone the next morning.

**Why this test matters as a contract pin.** This is the only test in the entire suite
where `_today() == expiry` exactly. It is the only test where `>` and `>=` produce
different results. If a future developer changes `>` to `>=` — perhaps thinking
"expires today means it has expired today" — this test fails immediately. Without it, the
operator change would pass all other tests silently, and entries would start disappearing
one day early, with no test catching it.

---

### Test 2 — `test_entry_expiring_yesterday_is_expired` PASSED

```python
def test_entry_expiring_yesterday_is_expired(self, pin_today):
    # today > expiry: _today() > expiry is True → entry is DROPPED.
    # This is the "day after expiry" case — the first day the entry is gone.
    entry = "[EPHEMERAL expires=2026-04-14] expired yesterday"
    assert _is_expired(entry) is True
```

**What it tests.** `_today()` returns `date(2026, 4, 15)`. The entry's expiry date is
`date(2026, 4, 14)`. The comparison is `date(2026, 4, 15) > date(2026, 4, 14)` which is
`True`. So `_is_expired` returns `True`.

**What PASSED proves.** The entry expired yesterday is correctly identified as expired.
This is the "day after expiry" case — the first day the entry is no longer present.
Combined with test 1, the two tests together precisely bracket the `>` boundary:
`expiry_date` → kept, `expiry_date + 1 day` → dropped. The boundary is at the day
transition.

**Relationship to the pre-existing `test_is_expired_past_date`.** That test uses
`date.today() - timedelta(days=1)` which also represents "yesterday", but relative to
the real clock. This test uses the fixed `2026-04-14` (one day before `FIXED_TODAY`). The
semantic is identical; the mechanism is clock-independent. This test does not replace
`test_is_expired_past_date` — both remain — but this test also pins the exact date value,
making the boundary relationship to test 1 unambiguous.

---

### Test 3 — `test_entry_expiring_tomorrow_is_not_expired` PASSED

```python
def test_entry_expiring_tomorrow_is_not_expired(self, pin_today):
    # today < expiry: still in the future → entry is KEPT.
    # Equivalent to existing test_is_expired_future_date but clock-independent.
    entry = "[EPHEMERAL expires=2026-04-16] expires tomorrow"
    assert _is_expired(entry) is False
```

**What it tests.** `_today()` returns `date(2026, 4, 15)`. The entry's expiry date is
`date(2026, 4, 16)`. The comparison is `date(2026, 4, 15) > date(2026, 4, 16)` which is
`False`. So `_is_expired` returns `False`.

**What PASSED proves.** Future entries are kept. Combined with tests 1 and 2, the three
tests together form a complete picture of the boundary:

| Entry expiry | Relative to today | `_today() > expiry` | Result |
|---|---|---|---|
| `2026-04-14` | yesterday | `True` | Dropped |
| `2026-04-15` | today (= boundary) | `False` | Kept |
| `2026-04-16` | tomorrow | `False` | Kept |

**Relationship to the pre-existing `test_is_expired_future_date`.** That test uses
`date.today() + timedelta(days=10)` — "clearly future", not adjacent to the boundary.
This test uses `2026-04-16` — exactly one day after `FIXED_TODAY`. It provides the
tightest possible "future kept" assertion adjacent to the boundary, and does so without
touching the real clock.

**Note on redundancy.** Test 3 is technically covered by `test_is_expired_future_date`
in spirit. It is included here because:
1. Clock-independence: all three boundary tests use the same pinned date.
2. The trio (tests 1, 2, 3) reads as a coherent unit: yesterday/today/tomorrow.
3. `days=10` in the pre-existing test proves "far future is kept" but not "day after
   expiry is kept."

---

### Test 4 — `test_load_from_disk_drops_exactly_expired_entries` PASSED

```python
def test_load_from_disk_drops_exactly_expired_entries(self, pin_today, tmp_path, monkeypatch):
    """load_from_disk() prunes yesterday but keeps today and tomorrow."""
    monkeypatch.setattr("tools.memory_tool.MEMORY_DIR", tmp_path)
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)

    mem_file = tmp_path / "MEMORY.md"
    entries = [
        "[EPHEMERAL expires=2026-04-14] yesterday",  # dropped
        "[EPHEMERAL expires=2026-04-15] today",      # kept
        "[EPHEMERAL expires=2026-04-16] tomorrow",   # kept
    ]
    mem_file.write_text(ENTRY_DELIMITER.join(entries), encoding="utf-8")

    s = MemoryStore(memory_char_limit=2200, user_char_limit=1375)
    s.load_from_disk()

    surviving = s.memory_entries
    assert len(surviving) == 2
    assert any("today" in e for e in surviving)
    assert any("tomorrow" in e for e in surviving)
    assert not any("yesterday" in e for e in surviving)

    # Expired entry must also be absent from the re-written file on disk.
    on_disk = mem_file.read_text(encoding="utf-8")
    assert "yesterday" not in on_disk
    assert "today" in on_disk
    assert "tomorrow" in on_disk
```

**What it tests.** The full `load_from_disk()` expiry-pruning pipeline at lines 239-245:

```python
# Drop expired ephemeral entries on load; persist if any were removed
for target in ("memory", "user"):
    entries = self._entries_for(target)
    filtered = [e for e in entries if not _is_expired(e)]
    if len(filtered) < len(entries):
        self._set_entries(target, filtered)
        self.save_to_disk(target)        # ← re-writes MEMORY.md without expired entries
```

The test constructs a real MEMORY.md with three entries and drives `load_from_disk()`.
It then checks **both** the in-memory state (`s.memory_entries`) and **the file on disk**
(`mem_file.read_text()`).

**What PASSED proves.**

*In-memory pruning.* `s.memory_entries` contains exactly 2 entries after `load_from_disk`.
The `"yesterday"` entry (expiry 2026-04-14) was removed. The `"today"` entry (expiry
2026-04-15) and `"tomorrow"` entry (expiry 2026-04-16) were retained. This confirms that
`_is_expired` is called on every entry and that only entries where `_today() > expiry`
are removed.

*File persistence.* `save_to_disk(target)` is called when any entry is removed. The file
on disk is re-written without the expired entry. This is critical: if only the in-memory
state were updated, the expired entry would reappear on the next `load_from_disk()`.
Verifying the file content proves the entire pruning contract: expired entries are
permanently removed from disk, not just hidden for the current session.

*The `if len(filtered) < len(entries)` guard.* The test setup ensures exactly one entry
is expired, so `save_to_disk` is called exactly once. If the guard were missing (i.e.,
`save_to_disk` always called), the test would still pass but would mask a performance
regression (unnecessary file writes on every load). The current assertions do not verify
the guard's efficiency — they only verify correctness.

**Why the boundary matters here.** The three-entry setup (`yesterday`/`today`/`tomorrow`)
directly reproduces the exact boundary scenario. Any off-by-one error in the pruning loop
would be visible: if the loop used `>=` instead of `>`, the `"today"` entry would also
be dropped, failing `assert len(surviving) == 2` with `assert 1 == 2`.

**What this test proves that tests 1-3 do not.** Tests 1-3 call `_is_expired` directly
and in isolation. Test 4 proves that `_is_expired` is actually called correctly *within*
`load_from_disk`, that the result is used to filter entries, and that filtered entries are
written back to disk. A bug that rewired `load_from_disk` to skip the expiry filter
entirely would not be caught by tests 1-3 but would be caught by test 4.

---

### Test 5 — `test_reload_target_drops_exactly_expired_entries` PASSED

```python
def test_reload_target_drops_exactly_expired_entries(self, pin_today, tmp_path, monkeypatch):
    """_reload_target() (called by add()) also prunes expired entries."""
    monkeypatch.setattr("tools.memory_tool.MEMORY_DIR", tmp_path)
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)

    mem_file = tmp_path / "MEMORY.md"
    entries = [
        "[EPHEMERAL expires=2026-04-14] yesterday",  # dropped
        "[EPHEMERAL expires=2026-04-15] today",      # kept
    ]
    mem_file.write_text(ENTRY_DELIMITER.join(entries), encoding="utf-8")

    s = MemoryStore(memory_char_limit=2200, user_char_limit=1375)
    s.load_from_disk()

    # Add a new entry — this triggers _reload_target("memory") before writing.
    s.add("memory", "a new normal entry")

    surviving = s.memory_entries
    assert not any("yesterday" in e for e in surviving)
    assert any("today" in e for e in surviving)
    assert any("a new normal entry" in e for e in surviving)
```

**What it tests.** The second expiry-pruning path — `_reload_target()` at lines 292-301:

```python
def _reload_target(self, target: str):
    """Re-read entries from disk into in-memory state.

    Called under file lock to get the latest state before mutating.
    Also drops expired ephemeral entries on reload.
    """
    fresh = self._read_file(self._path_for(target))
    fresh = list(dict.fromkeys(fresh))  # deduplicate
    fresh = [e for e in fresh if not _is_expired(e)]    # ← expiry filter
    self._set_entries(target, fresh)
```

`_reload_target` is called inside `add()` — specifically inside the file-lock block at
line 108-110:

```python
with self._file_lock(self._path_for(target)):
    # Re-read from disk under lock to pick up writes from other sessions
    self._reload_target(target)
```

The test triggers `_reload_target` indirectly by calling `s.add("memory", "a new normal entry")`.

**Why `add()` calls `_reload_target`.** `add()` takes a file lock before writing to
prevent race conditions in multi-session use (e.g., a CLI session and a gateway session
adding entries simultaneously). Under the lock, it re-reads the file to pick up any
writes from other sessions since the last `load_from_disk()`. This re-read also runs the
expiry filter, so expired entries written by another session are pruned before the new
entry is appended.

**What PASSED proves.**

*`_reload_target` prunes expired entries.* After `load_from_disk()` and then `add()`,
the `"yesterday"` entry (expiry 2026-04-14) is absent from `s.memory_entries`. The expiry
filter in `_reload_target` ran and removed it. Without the filter at line 300, the
`"yesterday"` entry would be present in `surviving`, failing
`assert not any("yesterday" in e for e in surviving)`.

*The new entry was added successfully after pruning.* `"a new normal entry"` appears in
`surviving`. This proves that after `_reload_target` drops the expired entry, the `add()`
call proceeds to append the new entry and succeed. The pruning does not interfere with the
write.

*`_today` is called from `_reload_target`, not only from `load_from_disk`.* The
`pin_today` fixture patches the module-level `_today()` function. If the expiry check in
`_reload_target` called `date.today()` directly (bypassing `_today()`), the patch would
have no effect and the test would fail because `date.today()` (April 11, 2026) is still
less than the expiry date `2026-04-14`. The test passing confirms that `_reload_target`
calls `_is_expired`, which calls `_today()`, which is the patched function.

**Why this test is separate from test 4.** Test 4 exercises `load_from_disk()` lines
239-245. Test 5 exercises `_reload_target()` lines 298-301. These are two independent
code paths that both call `_is_expired`. If the expiry filter were removed from one but
not the other, only the corresponding test would fail. Having separate tests for each
path means failure localises to the specific path.

**What test 5 does not verify.** It does not assert that `save_to_disk` was called (the
pruning in `_reload_target` does not call `save_to_disk` — `add()` does that after
building the new entry list). If the expired entry re-appeared in the file between
`_reload_target` and `add()`'s own `save_to_disk`, it would survive on disk. In practice
this cannot happen because both operations happen under the same file lock, but the test
only checks in-memory state, not the final file content. This is a deliberate scope
choice: testing file content after `add()` would require more assertions but would not
add meaningful coverage beyond test 4 (which already verifies the disk write path).

---

## 5. What All 5 Tests Together Prove

Reading the five tests as a unit, here is what can be asserted with confidence:

**The `>` (strict greater-than) contract is explicitly pinned.** The test `test_entry_expiring_today_is_not_expired` is the only test in the entire 156-test suite where `_today() == expiry`. It pins the observable consequence of using `>` rather than `>=`. Any future change to this operator will immediately fail a named test with a clear description.

**The exact boundary is documented in executable form.** The three-day sequence (`yesterday` → dropped, `today` → kept, `tomorrow` → kept) is written in test data with fixed calendar dates, not relative arithmetic. A reader can verify the expected behaviour by reading the test without running it, without consulting the source code, and without knowing what today's date is.

**Both expiry code paths are independently verified.** `load_from_disk()` (lines 239-245) and `_reload_target()` (lines 298-301) each have their own integration test. A refactoring that moves the filter from one path to the other would fail one of the two integration tests.

**Expired entries are pruned from disk, not just hidden.** Test 4 verifies that `save_to_disk()` is called after pruning and that the file on disk no longer contains the expired entry. This is the persistence contract: after a `load_from_disk()` that prunes at least one entry, the pruned entry cannot return on the next `load_from_disk()`.

**The monkeypatch infrastructure is correct.** The `pin_today` fixture plus the `MEMORY_DIR`/`get_memory_dir` patches together create a fully isolated environment. `_today()`, `load_from_disk()`, `_reload_target()`, `save_to_disk()`, and `_path_for()` all observe the pinned date and the redirected directory. No test touches the real home directory or the real system clock.

---

## 6. What the 5 Tests Do Not Prove

These gaps are worth tracking. They are not bugs — they are the boundaries of what these
automated tests can verify.

**1. The disk-write path in `_reload_target`.**
`_reload_target` calls `_set_entries` but does not call `save_to_disk`. Only `add()`'s
post-write does that. Test 5 verifies in-memory state only. If `add()` were changed to
skip `save_to_disk` on no-net-change cases, the expired entry might survive on disk for
one more `load_from_disk` cycle. Not tested.

**2. Malformed expiry date strings.**
`_is_expired` contains a `ValueError` guard:
```python
try:
    expiry = date.fromisoformat(m.group(1))
except ValueError:
    return False
```
If the prefix is `[EPHEMERAL expires=not-a-date]`, `date.fromisoformat` raises
`ValueError` and `_is_expired` returns `False` — the entry is kept, not pruned. This is
tested implicitly by `test_is_expired_normal_entry` (no prefix at all → `False`) but no
test passes a structurally valid prefix with an invalid date string (e.g.,
`expires=2026-13-01` for month 13, or `expires=2026-04-40` for day 40). These would pass
the regex but fail `fromisoformat`. The `ValueError` guard would silently keep them
forever.

**3. The `user` target.**
Tests 4 and 5 only write to and read from `MEMORY.md` (the `"memory"` target). The
`_reload_target` path for `"user"` is not directly exercised by these tests. The logic is
identical — both targets call `_is_expired` in the same loop — but a target-specific
bug (e.g., a wrong `_path_for` return for `"user"`) would not be caught here.

**4. Concurrent expiry across two sessions.**
`_reload_target` is the multi-session-safe read path — it runs under a file lock and
re-reads the file to pick up writes from other processes. If session A adds an entry while
session B is running `load_from_disk()` and that entry has an expiry that happens to fall
before session B's re-read time, it would be immediately pruned by session B's
`_reload_target`. Not tested.

**5. Timezone effects.**
`_today()` uses `date.today()`, which is wall-clock local time. A server running UTC
where the user is in UTC-8: at 11 PM local time, `date.today()` in UTC has already
advanced to the next calendar day. An entry the user added at 11 PM local time with a
1-day expiry would expire one day earlier from the user's perspective. The monkeypatch
approach used here cannot address this because `date(2026, 4, 15)` has no timezone
component — it is always "April 15" regardless of what timezone the server is in.
Fixing this would require `_today()` to accept a timezone argument, which is a behaviour
change beyond test coverage.

**6. The `user` entries in `load_from_disk` loop.**
The loop in `load_from_disk` iterates over both `"memory"` and `"user"` targets:
```python
for target in ("memory", "user"):
    entries = self._entries_for(target)
    filtered = [e for e in entries if not _is_expired(e)]
    if len(filtered) < len(entries):
        self._set_entries(target, filtered)
        self.save_to_disk(target)
```
Test 4 writes only to `MEMORY.md`. The `"user"` iteration runs over an empty list and
does nothing. No test writes expired ephemeral entries to `USER.md` and verifies they are
pruned. The code path is identical to the `"memory"` path, so it is highly unlikely to
be broken — but it is not directly tested.

**7. Performance: large history files.**
No test measures how long expiry pruning takes on a `MEMORY.md` with 1,000 entries. Each
`_reload_target` call reads the full file and rebuilds the entry list. Not benchmarked.

---

## 7. How to Reproduce the Run

```bash
cd /path/to/hermes-agent

# Just the expiry edge-case tests
python -m pytest tests/tools/test_memory_tool.py::TestExpiryEdgeCases -v --override-ini="addopts="

# All memory_tool tests (includes TestExpiryEdgeCases + 60 pre-existing tests)
python -m pytest tests/tools/test_memory_tool.py -v --override-ini="addopts="
# Expected: 65 passed, 1 warning

# Full regression — all three test files
python -m pytest \
  tests/tools/test_memory_tool.py \
  tests/tools/test_skill_manager_tool.py \
  tests/hermes_cli/test_rollback.py \
  -q --override-ini="addopts="
# Expected: 156 passed, 1 warning

# Verify the monkeypatch is doing something — run with the fixture removed.
# If you comment out `pin_today` from tests 4 and 5 and adjust the entry
# dates to relative ones (date.today() ± ...), the tests still pass because
# the logic is correct. But if you remove the pin from tests 1 and 2 and
# keep the fixed dates 2026-04-14 / 2026-04-15, those tests will fail
# when run before 2026-04-15 (today < fixed date → boundary semantics change).
```

---

## 8. Source Files Referenced

| File | Role |
|------|------|
| `tests/tools/test_memory_tool.py:444` | `FIXED_TODAY` constant |
| `tests/tools/test_memory_tool.py:447` | `pin_today` fixture |
| `tests/tools/test_memory_tool.py:453` | `TestExpiryEdgeCases` class (5 tests) |
| `tools/memory_tool.py:131` | `_today()` — the function being monkeypatched |
| `tools/memory_tool.py:155` | `_is_expired()` — the function under test in tests 1-3 |
| `tools/memory_tool.py:231` | `load_from_disk()` — function under test in test 4 |
| `tools/memory_tool.py:239` | Expiry-pruning loop inside `load_from_disk()` |
| `tools/memory_tool.py:292` | `_reload_target()` — function under test in test 5 |
| `tools/memory_tool.py:300` | Expiry filter inside `_reload_target()` |
| `tools/memory_tool.py:221` | `MemoryStore.__init__` — constructor signature |
| `tools/memory_tool.py:59` | `get_memory_dir()` — patched to redirect I/O to `tmp_path` |
| `tools/memory_tool.py:66` | `MEMORY_DIR` — backward-compat constant, also patched |
| `tools/memory_tool.py:68` | `ENTRY_DELIMITER` — `"\n§\n"` separator used in test setup |
| `tests/tools/test_memory_tool.py:302` | `test_ephemeral_expires_on_reload` — pre-existing test, correct monkeypatch pattern |
| `tests/tools/test_memory_tool.py:422` | `test_is_expired_past_date` — pre-existing, clock-dependent, not boundary-testing |
| `tests/tools/test_memory_tool.py:426` | `test_is_expired_future_date` — pre-existing, clock-dependent, not boundary-testing |
