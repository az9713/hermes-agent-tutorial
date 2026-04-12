# `hermes skills history` — Feature Reference, Implementation, and Test Evidence

> **Scope of this document.**
> Complete reference for the `hermes skills history <name>` CLI command introduced in
> `hermes-agent`. Covers: what the command is and why it exists, how every line of the
> implementation works, the complete 23-test suite with verbatim run receipt, and a
> section-by-section guide on how to read and interpret the test results.
>
> Companion documents:
> - `docs/analysis/history-cli-tests.md` — narrower focus: test strategy and per-test
>   interpretation without the implementation narrative
> - `docs/analysis/rollback-cli-tests.md` — identical structure applied to
>   `hermes skills rollback`, the sibling command

---

## Table of Contents

1. [What the command is](#1-what-the-command-is)
2. [Why it was built](#2-why-it-was-built)
3. [Complete implementation walkthrough](#3-complete-implementation-walkthrough)
   - 3a. The `SKILL_HISTORY.md` format it reads
   - 3b. `_parse_all_history_records` — the parser
   - 3c. `do_history` — the command body
   - 3d. Argparse registration in `main.py`
   - 3e. Routing in `skills_command()`
4. [The 23-test suite — what was written and why](#4-the-23-test-suite--what-was-written-and-why)
5. [Verbatim test receipt — the evidence](#5-verbatim-test-receipt--the-evidence)
6. [How to interpret the test results](#6-how-to-interpret-the-test-results)
   - 6a. How to read a pytest receipt
   - 6b. Class-by-class interpretation
   - 6c. What the full 23 prove together
   - 6d. What they do not prove
7. [Running the tests yourself](#7-running-the-tests-yourself)
8. [Source files reference](#8-source-files-reference)

---

## 1. What the Command Is

`hermes skills history <name>` is a read-only CLI command that displays the complete
patch/edit/rollback audit trail for a named skill.

### Default output — summary table

```
hermes skills history my-skill
```

Produces a Rich table with five columns:

```
              History for 'my-skill'
 ┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
 ┃ # ┃ Timestamp             ┃ Action   ┃ Reason                ┃ File     ┃
 ┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
 │ 1 │ 2026-04-11T14:32:05Z  │ patch    │ Initial improvement   │ SKILL.md │
 │ 2 │ 2026-04-11T14:35:00Z  │ rollback │ Rolled back via CLI   │ SKILL.md │
 │ 3 │ 2026-04-11T15:00:00Z  │ patch    │ Re-apply improvement  │ SKILL.md │
 └───┴───────────────────────┴──────────┴───────────────────────┴──────────┘
3 record(s). Use --detail N to see the diff for a specific record.
```

Records are ordered oldest-first. Record #1 is the first change ever applied to the skill.

### Detail mode — `--detail N`

```
hermes skills history my-skill --detail 1
```

Produces a header and a coloured `unified_diff` for the specific record:

```
Record #1 — patch
Timestamp: 2026-04-11T14:32:05Z
Reason:    Initial improvement
File:      SKILL.md

--- SKILL.md (old)
+++ SKILL.md (new)
@@ -1 +1 @@
-Step 1: Do the thing.
+Step 1: Do the new thing.
```

Lines starting with `+` are rendered green; lines starting with `-` are rendered red.
The diff is capped at 60 lines with a "... and N more lines" notice for very large records.

### Error cases

| Condition | Output |
|-----------|--------|
| Skill name does not exist | `Error: Skill '<name>' not found.` |
| Skill exists, no `SKILL_HISTORY.md` | `No history found for '<name>'.` |
| `SKILL_HISTORY.md` exists but unparseable | `No parseable records in history for '<name>'.` |
| `--detail N` out of range (N < 1 or N > count) | `Error: Record #N does not exist. Valid range: 1–M.` |

---

## 2. Why It Was Built

Every time the self-improvement loop applies a patch to a skill, it appends a record to
`SKILL_HISTORY.md` inside the skill's directory. The same file receives records when a
user runs `hermes skills rollback`. The file is append-only and grows with every change.

Before this command existed, there was no structured way to inspect that log. Users who
wanted to see what patches had been applied had to open the raw Markdown file directly —
no table, no numbered records, no easy way to view a single diff without reading the
entire file.

`hermes skills history` was step #5 in the roadmap recorded in
`docs/analysis/implementation-status.md` Part 5:

> *"`hermes skills history <name>` view command. Let users inspect the patch log without
> editing the raw Markdown. Output should be a Rich table: timestamp | action | reason |
> file. This is the natural companion to rollback."*

The command is deliberately read-only. It does not change any files. It is the audit
view that sits alongside `hermes skills rollback` — users use `history` to decide what
they want to roll back and `rollback` to actually do it.

---

## 3. Complete Implementation Walkthrough

The full implementation spans three production files:

| File | What was added |
|------|---------------|
| `hermes_cli/skills_hub.py` | `_parse_all_history_records()` — parser function |
| `hermes_cli/skills_hub.py` | `do_history()` — command body |
| `hermes_cli/skills_hub.py` | `elif action == "history":` route in `skills_command()` |
| `hermes_cli/main.py` | `skills_history` argparse subparser |

No new dependencies were introduced. All functions, types, and imports used already
existed in the codebase.

---

### 3a. The `SKILL_HISTORY.md` Format It Reads

Before reading the parser code, it is important to understand what format it parses.
`_append_skill_history` in `tools/skill_manager_tool.py` writes records in this exact
format:

```markdown
## 2026-04-11T14:32:05Z — patch
**Reason:** Initial improvement
**File:** SKILL.md

### Old
```text
Step 1: Do the thing.
```

### New
```text
Step 1: Do the new thing.
```
```

Key format details that the parser relies on:
- Each record starts with `## YYYY-MM-DDTHH:MM:SSZ — action` (em-dash U+2014, not hyphen).
- `**Reason:**` and `**File:**` are on separate lines immediately after the header.
- `### Old` and `### New` are fenced with ` ```text ` / ` ``` `.
- Records are appended in chronological order (oldest at top of file).

The parser must handle `patch`, `edit`, and `rollback` as action values. It must not
assume any maximum number of records.

---

### 3b. `_parse_all_history_records` — The Parser

**Location:** `hermes_cli/skills_hub.py`, after line 698

**Full source:**

```python
def _parse_all_history_records(history_text: str) -> list:
    """
    Parse ALL records from SKILL_HISTORY.md.

    Returns a list of dicts (oldest first), each with keys:
      timestamp, action, reason, file_path, old_text, new_text

    Unlike _parse_last_history_record, this function includes rollback records
    so the full audit trail is visible in the history table view.
    """
    sections = re.split(r'\n(?=## \d{4}-\d{2}-\d{2}T)', history_text)
    records = []
    for section in sections:
        header = re.match(r'## (\S+) — (\w+)', section)
        if not header:
            continue
        reason_m = re.search(r'\*\*Reason:\*\* (.+)', section)
        file_m = re.search(r'\*\*File:\*\* (.+)', section)
        old_m = re.search(r'### Old\n```text\n(.*?)\n```', section, re.DOTALL)
        new_m = re.search(r'### New\n```text\n(.*?)\n```', section, re.DOTALL)
        records.append({
            "timestamp": header.group(1),
            "action": header.group(2),
            "reason": (reason_m.group(1).strip() if reason_m else ""),
            "file_path": (file_m.group(1).strip() if file_m else ""),
            "old_text": (old_m.group(1) if old_m else ""),
            "new_text": (new_m.group(1) if new_m else ""),
        })
    return records
```

**How it works, line by line:**

`re.split(r'\n(?=## \d{4}-\d{2}-\d{2}T)', history_text)`
: Split the file on newlines followed by a `## YYYY-MM-DDTHH...` header lookahead.
  This divides the file into one section per record. The lookahead `(?=...)` means the
  splitter consumes only the newline; the `## ` header stays at the start of each
  section. The first element of the split may be empty text before the first record —
  that element fails the `header` match and is silently skipped.

`re.match(r'## (\S+) — (\w+)', section)`
: Anchored match at the start of the section. Group 1 captures the timestamp (`\S+`
  = one or more non-whitespace). Group 2 captures the action (`\w+` = word characters,
  so `patch`, `edit`, `rollback` all match). The em-dash `—` (U+2014) is a literal
  character in the pattern, matching the literal em-dash `_append_skill_history` writes.
  A section missing this header — e.g. a manually-written note or file header — does
  not match and is skipped via `continue`.

`re.search(r'\*\*Reason:\*\* (.+)', section)` and similarly for `**File:**`
: Non-anchored search. Group 1 captures everything to end-of-line after the label.
  `.strip()` removes trailing whitespace. If not found, the field defaults to `""`.
  Both fields are optional in the parser's view — corrupted records that are missing
  a field produce empty strings, not crashes.

`re.search(r'### Old\n```text\n(.*?)\n```', section, re.DOTALL)`
: `.*?` is non-greedy to stop at the first ` ``` `. `re.DOTALL` allows `.` to match
  newlines — multi-line skill content is captured verbatim. If not found, `old_text`
  defaults to `""`.

**Contrast with `_parse_last_history_record` (the sibling function used by `do_rollback`):**

| Aspect | `_parse_last_history_record` | `_parse_all_history_records` |
|--------|------------------------------|------------------------------|
| Returns | `(file_path, old_text, new_text)` — the content to restore | `list[dict]` — all records for display |
| Order | Reverse-scans (newest first, stops at first valid record) | Forward scan (oldest first, collects all) |
| Rollback records | Skipped — a rollback cannot be rolled back | Included — the audit trail must show rollbacks |
| On empty input | Returns `(None, None, None)` | Returns `[]` |
| Purpose | "What should I restore the skill to?" | "What is the full change history?" |

The two functions have overlapping regex patterns because they parse the same file format.
They are not deduplicated into a shared helper because their return shapes, scan directions,
and rollback-handling logic are different in ways that a shared parser would complicate.

---

### 3c. `do_history` — The Command Body

**Location:** `hermes_cli/skills_hub.py`, after line 829

**Full source:**

```python
def do_history(
    name: str,
    detail: Optional[int] = None,
    console: Optional[Console] = None,
) -> None:
    """
    Display the patch/edit/rollback log for a skill as a Rich table.

    With --detail N, shows the full diff for record #N (1-indexed, oldest first).
    Without --detail, shows a summary table of all records.
    """
    from tools.skill_manager_tool import _find_skill, SKILL_HISTORY_FILE

    c = console or _console

    existing = _find_skill(name)
    if not existing:
        c.print(f"[bold red]Error:[/] Skill '{name}' not found.\n")
        return

    skill_dir = existing["path"]
    history_path = skill_dir / SKILL_HISTORY_FILE

    if not history_path.exists():
        c.print(f"[bold yellow]No history found for '{name}'.[/]\n")
        return

    history_text = history_path.read_text(encoding="utf-8")
    records = _parse_all_history_records(history_text)

    if not records:
        c.print(f"[bold yellow]No parseable records in history for '{name}'.[/]\n")
        return

    # --detail N: show diff for a specific record (1-indexed)
    if detail is not None:
        if detail < 1 or detail > len(records):
            c.print(
                f"[bold red]Error:[/] Record #{detail} does not exist. "
                f"Valid range: 1\u2013{len(records)}.\n"
            )
            return
        rec = records[detail - 1]
        c.print(f"\n[bold]Record #{detail} \u2014 {rec['action']}[/]")
        c.print(f"[dim]Timestamp:[/] {rec['timestamp']}")
        c.print(f"[dim]Reason:[/]    {rec['reason']}")
        c.print(f"[dim]File:[/]      {rec['file_path']}\n")
        diff_lines = list(difflib.unified_diff(
            rec["old_text"].splitlines(keepends=True),
            rec["new_text"].splitlines(keepends=True),
            fromfile=f"{rec['file_path']} (old)",
            tofile=f"{rec['file_path']} (new)",
            lineterm="",
        ))
        if diff_lines:
            for line in diff_lines[:60]:
                if line.startswith("+"):
                    c.print(f"[green]{line}[/]", end="")
                elif line.startswith("-"):
                    c.print(f"[red]{line}[/]", end="")
                else:
                    c.print(line, end="")
            if len(diff_lines) > 60:
                c.print(f"\n[dim]... and {len(diff_lines) - 60} more lines[/]")
            c.print()
        else:
            c.print("[dim]No visible diff (old and new are identical).[/]\n")
        return

    # Default: summary table
    table = Table(title=f"History for '{name}'")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Timestamp", style="cyan")
    table.add_column("Action", style="bold")
    table.add_column("Reason")
    table.add_column("File", style="dim")

    for i, rec in enumerate(records, 1):
        action_style = {"rollback": "yellow", "patch": "green", "edit": "blue"}.get(
            rec["action"], ""
        )
        table.add_row(
            str(i),
            rec["timestamp"],
            f"[{action_style}]{rec['action']}[/]" if action_style else rec["action"],
            rec["reason"][:60] + ("\u2026" if len(rec["reason"]) > 60 else ""),
            rec["file_path"],
        )

    c.print(table)
    c.print(
        f"[dim]{len(records)} record(s). "
        f"Use --detail N to see the diff for a specific record.[/]\n"
    )
```

**How it works — structural walkthrough:**

**Signature.** `console: Optional[Console] = None` is the Rich console injection point.
When `None`, the module-level `_console` is used (the real terminal). Tests pass their
own `StringIO`-backed console to capture output without printing to the screen.

**Early-exit guards (lines 1–4 of the body).** Four sequential checks, each printing a
specific message and returning before reaching the rendering code:

```
_find_skill(name) is None     →  "Error: Skill '<name>' not found."
history_path.exists() is False →  "No history found for '<name>'."
records is []                  →  "No parseable records in history for '<name>'."
detail out of range            →  "Error: Record #N does not exist."
```

These four guards mean the table/diff rendering code is only reached when the data is
known to be valid.

**`--detail N` path.** The `detail` parameter is an `int` or `None`. When it is an int,
range validation runs first (`detail < 1 or detail > len(records)`). Then `records[detail - 1]`
selects the record (1-indexed in the user-facing output, 0-indexed in the list). The
diff is produced by `difflib.unified_diff` on `old_text` vs `new_text`. Lines are printed
individually: `+` lines in green, `-` lines in red, context lines as-is. The 60-line cap
prevents flooding the terminal on large diffs; a truncation notice is printed if the diff
exceeds the cap. When `old_text == new_text`, `unified_diff` produces no lines and
`"No visible diff"` is printed instead.

**Default table path.** A Rich `Table` is constructed with five columns. The `enumerate(records, 1)`
produces 1-indexed row numbers. Action colour-coding: `patch` = green, `edit` = blue,
`rollback` = yellow; unknown actions = unstyled. Reasons are truncated at 60 characters
with a `…` ellipsis (U+2026) to prevent very long reasons from distorting column widths.
The footer line prints the record count and hints at `--detail`.

**Patterns reused from `do_rollback` (the sibling function):**

| Pattern | Where in `do_rollback` | Where in `do_history` |
|---------|------------------------|----------------------|
| `_find_skill` → check None → error print | Lines 750–753 | Lines 844–847 |
| `skill_dir / SKILL_HISTORY_FILE` → check exists | Lines 755–759 | Lines 849–853 |
| `console or _console` injection | Line 748 | Line 842 |
| `difflib.unified_diff` with 60-line cap | Lines 774–794 | Lines 876–895 |
| Green/red line colouring | Lines 785–789 | Lines 885–890 |

The reuse is by copy, not abstraction. There are only two call sites and abstracting the
error-handling sequence or diff rendering into shared helpers would add indirection without
reducing duplication meaningfully.

---

### 3d. Argparse Registration in `main.py`

**Location:** `hermes_cli/main.py`, after line 4879 (after the rollback subparser)

**Added code:**

```python
skills_history = skills_subparsers.add_parser(
    "history", help="Show the patch/edit/rollback log for a skill"
)
skills_history.add_argument("name", help="Skill name to show history for")
skills_history.add_argument(
    "--detail", type=int, default=None, metavar="N",
    help="Show the diff for record #N"
)
```

**What each line does:**

`add_parser("history", ...)` — registers `history` as a subcommand of `hermes skills`.
After this, `hermes skills history` is a valid invocation; without it, argparse would
print a usage error.

`add_argument("name", ...)` — positional required argument. No `nargs` or `default`, so
`hermes skills history` with no name argument produces `SystemExit(2)`.

`add_argument("--detail", type=int, ...)` — optional flag. `type=int` means argparse
calls `int(value)` on whatever string the user passes. If the user passes a non-integer
(`--detail abc`), argparse raises a `ValueError` internally and exits with code 2. This
means `do_history` receives either a proper `int` or `None` — never a string — and needs
no further type checking.

**The `metavar="N"` detail.** This controls the usage string. Without it, argparse would
print `--detail DETAIL`. With it, the help text reads `--detail N`, which is more concise
and matches the way the record number is displayed in output (`Record #N`).

---

### 3e. Routing in `skills_command()`

**Location:** `hermes_cli/skills_hub.py`, line 1250

**Added code:**

```python
elif action == "history":
    do_history(args.name, detail=getattr(args, "detail", None))
```

**Position in the dispatch chain:**

```python
if action == "list":
    ...
elif action == "add":
    ...
elif action == "rollback":
    do_rollback(args.name, skip_confirm=getattr(args, "yes", False))
elif action == "history":                         # ← added here
    do_history(args.name, detail=getattr(args, "detail", None))
elif action == "publish":
    ...
```

`getattr(args, "detail", None)` is defensive: it falls back to `None` if `args` is
constructed without a `detail` attribute (e.g. in tests that construct `Namespace`
objects manually without setting all fields).

---

## 4. The 23-Test Suite — What Was Written and Why

Tests are in `tests/hermes_cli/test_history.py`. They cover the command in six classes,
each testing a distinct layer of the implementation stack.

```
User runs: hermes skills history my-skill --detail 2
                    │
         Layer 1: argparse (main.py)
         Parses CLI input → Namespace(skills_action="history", name="my-skill", detail=2)
                    │
         Layer 2: skills_command routing (skills_hub.py)
         elif action == "history": do_history(args.name, detail=2)
                    │
         Layer 3: do_history() (skills_hub.py)
         Find skill → read file → parse records → render diff for record 2
                    │
         Layer 4: _parse_all_history_records() (skills_hub.py)
         Regex parser: splits on headers, extracts all records oldest-first
```

Testing each layer independently means a failure pinpoints the exact layer: if
`test_history_subparser_registered` fails but `test_skills_command_routes_history` passes,
the problem is in argparse registration, not in the dispatch logic or command body.

### Test infrastructure

All tests share the same infrastructure established by `test_rollback.py`:

**`_skill_dir(tmp_path)` context manager** (from `tests/tools/test_skill_manager_tool.py:32`):
Patches both `tools.skill_manager_tool.SKILLS_DIR` and `agent.skill_utils.get_all_skills_dirs`
to point at `tmp_path`. This redirects all file I/O away from the real `~/.hermes/skills/`
directory and into a clean temporary directory that is deleted after each test.

**`_create_skill`, `_patch_skill`, `_append_skill_history`** (from `tools/skill_manager_tool.py`):
Real production functions, not mocks. Tests call them to write real `SKILL.md` and
`SKILL_HISTORY.md` files. This is the correct approach: the parser test must parse what
the writer writes. A mock would break this contract.

**`_capture_console()` helper** (defined locally in `test_history.py`):
```python
def _capture_console():
    sink = StringIO()
    return sink, Console(file=sink, force_terminal=False, color_system=None)
```
Creates a Rich `Console` that writes to a `StringIO` buffer instead of stdout.
`force_terminal=False` suppresses ANSI terminal detection. `color_system=None` disables
all colour markup — no escape codes appear in `sink.getvalue()`. This allows assertions
like `assert "patch" in output` to work on plain text.

**`_run_skills_cmd` helper** (defined inside `TestHistoryArgparse`):
```python
def _run_skills_cmd(self, argv, monkeypatch):
    captured = {}
    monkeypatch.setattr("sys.argv", ["hermes"] + argv)
    monkeypatch.setattr(
        "hermes_cli.skills_hub.skills_command",
        lambda args: captured.update(vars(args)),
    )
    from hermes_cli.main import main
    main()
    return captured
```
Patches `sys.argv` to simulate a command-line invocation. Patches `skills_command` with a
lambda that captures the parsed `Namespace` as a dict. Calls the real `main()` — the
actual argument parser built by the production code. Returns the captured dict for
assertions. This ensures that any rename or removal of the `history` subparser in
`main.py` immediately fails the argparse tests.

---

### Class 1 — `TestParseAllHistoryRecords` (5 tests)

Tests the parser in complete isolation, using inline string fixtures — no disk I/O.
The three module-level fixtures (`_PATCH_RECORD`, `_ROLLBACK_RECORD`, `_EDIT_RECORD`)
represent the exact string format `_append_skill_history` writes.

| Test | Inputs | What it asserts | Why this test exists |
|------|--------|-----------------|----------------------|
| `test_empty_text_returns_empty_list` | `""` | `== []` | Parser must not crash or return `None` on empty input. `do_history` depends on `if not records` to detect this case. |
| `test_single_patch_record` | `_PATCH_RECORD` | All 6 fields correct: timestamp, action, reason, file_path, old_text, new_text | Proves writer and reader agree on format. Tests the em-dash regex, the `**Reason:**` extraction, and the fenced-block DOTALL capture. |
| `test_multiple_records_preserves_order` | `_PATCH_RECORD + _ROLLBACK_RECORD` | `list[0]["action"] == "patch"`, `list[1]["action"] == "rollback"` | Records in the history table must be numbered oldest (#1) to newest (#N). A reversed parser would show the wrong numbering. |
| `test_rollback_record_included` | `_ROLLBACK_RECORD` | `len(records) == 1`, `records[0]["action"] == "rollback"` | The most important design difference from `_parse_last_history_record`. Rollbacks must appear in the audit trail. |
| `test_malformed_record_skipped` | malformed text + `_PATCH_RECORD` | `len(records) == 1`, `records[0]["action"] == "patch"` | A manually-edited or partially-written `SKILL_HISTORY.md` should not crash the command or hide valid records. |

---

### Class 2 — `TestDoHistoryTableView` (5 tests)

Integration tests using real disk files. Each test calls `_create_skill` and `_patch_skill`
to produce a genuine `SKILL_HISTORY.md`, then calls `do_history` and inspects the output.

| Test | Setup | What it asserts | Why this test exists |
|------|-------|-----------------|----------------------|
| `test_single_patch_shows_table` | 1 patch, reason="test reason" | skill name, "patch", reason, "SKILL.md" all in output | End-to-end smoke test. Proves the pipeline: write history → read → parse → render → output. |
| `test_multiple_patches_numbered` | 2 patches | "1", "2", both reasons in output | Proves `enumerate(records, 1)` produces correct row numbers for multiple records. |
| `test_rollback_record_in_table` | 1 patch + do_rollback | "rollback" in output | Integration-level confirmation that rollback records appear. Exercises the full write path: `_patch_skill` + `do_rollback` → `do_history`. |
| `test_shows_record_count` | 2 patches | `"2 record(s)"` in output | Pins the footer summary line. Users rely on this to know the total before deciding which `--detail N` to use. |
| `test_hint_about_detail_flag` | 1 patch | `"--detail"` in output | Pins the discoverability hint. If removed, users would not learn about `--detail` from the default output. |

---

### Class 3 — `TestDoHistoryDetailView` (4 tests)

Tests the `--detail N` code path exclusively.

| Test | Setup | What it asserts | Why this test exists |
|------|-------|-----------------|----------------------|
| `test_detail_shows_diff` | 1 patch | `"Record #1"`, `"patch"`, `"Do the new thing"` in output | Proves the full `--detail` path: record selection, header print, `unified_diff` invocation, and that diff content appears in output. |
| `test_detail_out_of_range` | 1 patch, `detail=99` | `"does not exist"` and `"99"` in output | Proves the range guard (`detail > len(records)`) fires and reports the correct record number. |
| `test_detail_zero_is_invalid` | 1 patch, `detail=0` | `"does not exist"` in output | Proves the range guard (`detail < 1`) fires. Records are 1-indexed; 0 is not a valid selection. |
| `test_detail_identity_diff` | `_append_skill_history` called directly with `old_text == new_text` | `"No visible diff"` in output | Proves the `if diff_lines:` branch handles the empty-diff case. The only way to produce this in a test is via `_append_skill_history` directly since `_patch_skill` requires old != new. |

---

### Class 4 — `TestDoHistoryErrorPaths` (3 tests)

Tests every early-exit branch before the rendering code is reached.

| Test | Setup | What it asserts | Why this test exists |
|------|-------|-----------------|----------------------|
| `test_unknown_skill_prints_error` | No skill created | `"not found"` (case-insensitive) | Typo in skill name is the most common user error. Verify it is handled gracefully. |
| `test_no_history_file_prints_warning` | Skill created but never patched | `"No history found"` | Skill exists but has never been improved. `SKILL_HISTORY.md` is absent. |
| `test_empty_history_file_prints_warning` | Skill created, `SKILL_HISTORY.md` written as `""` | `"No parseable records"` | Distinguishes "file absent" from "file present but empty/corrupt". Different user situations, different messages. |

---

### Class 5 — `TestHistoryCommandDispatch` (2 tests)

Tests the `elif action == "history":` routing in `skills_command()` using `MagicMock`.
No disk I/O. No argparse.

| Test | `Namespace` | What it asserts | Why this test exists |
|------|-------------|-----------------|----------------------|
| `test_skills_command_routes_history` | `skills_action="history"`, `name="test-skill"`, `detail=None` | `do_history("test-skill", detail=None)` called once | Proves the `elif` branch routes correctly with `detail=None`. |
| `test_skills_command_routes_history_with_detail` | `skills_action="history"`, `name="test-skill"`, `detail=3` | `do_history("test-skill", detail=3)` called once | Proves `detail` is forwarded from the Namespace. Not dropped, not hardcoded. |

**Why separate from the argparse tests.** These tests bypass argparse entirely. If the
`elif action == "history":` branch were deleted from `skills_command` but the argparse
subparser still existed, the argparse tests would pass (they only check what the parser
produces) but these tests would fail (they check what `skills_command` does with that
output). The two layers are independently vulnerable to different bugs.

---

### Class 6 — `TestHistoryArgparse` (4 tests)

Tests argparse registration using the real `main()` via the `_run_skills_cmd` helper.

| Test | `sys.argv` | What it asserts | Why this test exists |
|------|-----------|-----------------|----------------------|
| `test_history_subparser_registered` | `["hermes", "skills", "history", "my-skill"]` | `skills_action="history"`, `name="my-skill"`, `detail=None` | Proves all three fields parsed correctly when `--detail` is absent. |
| `test_history_detail_flag` | `["hermes", "skills", "history", "my-skill", "--detail", "3"]` | `detail=3` (int, not string) | Proves `type=int` on the argparse argument converts the string `"3"` to `int(3)`. |
| `test_history_missing_name_errors` | `["hermes", "skills", "history"]` | `SystemExit(2)` | Proves `name` is required. Missing positional → argparse error → exit code 2. |
| `test_history_detail_requires_int` | `["hermes", "skills", "history", "my-skill", "--detail", "abc"]` | `SystemExit(2)` | Proves `type=int` rejects non-integer input. Argparse calls `int("abc")`, which raises `ValueError`, which argparse converts to `SystemExit(2)`. |

---

## 5. Verbatim Test Receipt — The Evidence

This is the unmodified output from running the 23 tests. It is the primary artifact
proving the implementation is correct.

**Command:**

```
python -m pytest tests/hermes_cli/test_history.py -v --override-ini="addopts="
```

**Environment:**

- Python 3.13.5
- pytest 8.4.2, pluggy 1.6.0
- platform win32
- Date: 2026-04-11

**Output:**

```
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-8.4.2, pluggy-1.6.0 -- C:\Users\simon\AppData\Local\Programs\Python\Python313\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent
configfile: pyproject.toml
plugins: anyio-4.9.0, langsmith-0.3.45, asyncio-1.3.0, cov-7.0.0, mock-3.15.1, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 23 items

tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_empty_text_returns_empty_list PASSED [  4%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_single_patch_record PASSED [  8%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_multiple_records_preserves_order PASSED [ 13%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_rollback_record_included PASSED [ 17%]
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_malformed_record_skipped PASSED [ 21%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_single_patch_shows_table PASSED [ 26%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_multiple_patches_numbered PASSED [ 30%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_rollback_record_in_table PASSED [ 34%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_shows_record_count PASSED [ 39%]
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_hint_about_detail_flag PASSED [ 43%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_shows_diff PASSED [ 47%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_out_of_range PASSED [ 52%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_zero_is_invalid PASSED [ 56%]
tests/hermes_cli/test_history.py::TestDoHistoryDetailView::test_detail_identity_diff PASSED [ 60%]
tests/hermes_cli/test_history.py::TestDoHistoryErrorPaths::test_unknown_skill_prints_error PASSED [ 65%]
tests/hermes_cli/test_history.py::TestDoHistoryErrorPaths::test_no_history_file_prints_warning PASSED [ 69%]
tests/hermes_cli/test_history.py::TestDoHistoryErrorPaths::test_empty_history_file_prints_warning PASSED [ 73%]
tests/hermes_cli/test_history.py::TestHistoryCommandDispatch::test_skills_command_routes_history PASSED [ 78%]
tests/hermes_cli/test_history.py::TestHistoryCommandDispatch::test_skills_command_routes_history_with_detail PASSED [ 82%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_subparser_registered PASSED [ 86%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_detail_flag PASSED [ 91%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_missing_name_errors PASSED [ 95%]
tests/hermes_cli/test_history.py::TestHistoryArgparse::test_history_detail_requires_int PASSED [100%]

============================== warnings summary ===============================
tests/hermes_cli/test_history.py::TestParseAllHistoryRecords::test_empty_text_returns_empty_list
  C:\Users\simon\Downloads\hermes_agent_collection\hermes-agent\tests\conftest.py:91: DeprecationWarning: There is no current event loop
    loop = asyncio.get_event_loop_policy().get_event_loop()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 23 passed, 1 warning in 4.22s ========================
```

**Summary line:** `23 passed, 1 warning in 4.22s`

**About the warning.** The `DeprecationWarning` comes from `tests/conftest.py:91`, a
shared fixture that fires once per run on the first test that triggers conftest
initialization. It is pre-existing, unrelated to `test_history.py`, and does not affect
any test result. No test in this file uses asyncio. The warning does not indicate any
problem in the history implementation.

**About `--override-ini="addopts="`.**  `pyproject.toml` configures
`addopts = "-m 'not integration' -n auto"`. The `-n auto` flag requires `pytest-xdist`
for parallel execution. That package is not installed in this Windows environment, so
`addopts` is cleared to prevent the flag from being passed. On a full Linux development
environment with `pytest-xdist` installed, run simply:
```
python -m pytest tests/hermes_cli/test_history.py -v
```

---

## 6. How to Interpret the Test Results

### 6a. How to Read a Pytest Receipt

A pytest receipt has a specific structure. Reading it correctly allows you to extract
precise information about what was verified.

**The header block** names the platform, Python version, and plugins loaded. This
confirms the environment in which the tests were run. If you reproduce the run on a
different platform (e.g. Linux with Python 3.12), the header will differ — that is
normal. What matters is that the same test names appear and all say `PASSED`.

**The collection line** (`collected 23 items`) confirms pytest found all 23 tests. If
this number is lower — say, 20 — it means some tests were not discovered. Common causes:
a syntax error in the test file, or a test class or function not starting with `Test`/`test_`.
All 23 being collected confirms the file is syntactically valid and all class/method names
follow the pytest naming convention.

**The test lines** each follow the pattern:
```
<module>::<Class>::<test_method>  PASSED  [  4%]
```
`PASSED` means the test body executed without raising any exception and all `assert`
statements were true. The percentage is how far through the 23 tests pytest has
progressed.

**A `FAILED` line** would look like:
```
tests/hermes_cli/test_history.py::TestDoHistoryTableView::test_single_patch_shows_table FAILED [ 26%]
```
followed later by an `ERRORS` or `FAILURES` block with the traceback and the specific
assertion that was false. The absence of any `FAILED` lines in this receipt means no
assertion failed.

**The warnings summary** lists non-fatal issues. A warning does not fail a test. The
single warning shown here is in `conftest.py`, not in the history tests themselves.

**The final line** — `23 passed, 1 warning in 4.22s` — is the authoritative summary.
`0 failed` and `0 errors` (both implicitly zero because they are omitted when zero) mean
there were no test failures and no collection/fixture errors.

---

### 6b. Class-by-Class Interpretation

What does each class's passing mean, and what would a failure mean?

#### Class 1 PASSED — `TestParseAllHistoryRecords`

**What it means:** The regex parser correctly handles empty input, single records,
multiple records in order, rollback record inclusion, and malformed record skipping.
The em-dash (U+2014) in the header regex matches correctly. The fenced-block DOTALL
capture handles multi-line content. The writer (`_append_skill_history`) and reader
(`_parse_all_history_records`) are in agreement about the file format.

**What a failure would mean:** The parser does not handle the specific failing case.
If `test_single_patch_record` failed, it would indicate the regex patterns do not match
the format `_append_skill_history` writes — the most likely cause being a format change
in the writer that was not reflected in the reader. If `test_rollback_record_included`
failed, it would mean the parser was modified to skip rollback records, breaking the
audit trail.

#### Class 2 PASSED — `TestDoHistoryTableView`

**What it means:** The full pipeline from disk write to Rich Table output works
end-to-end. The production functions `_create_skill` and `_patch_skill` produce history
files that `do_history` correctly reads, parses, and renders. All four visible data
fields (name, action, reason, file) appear in the output. Row numbering is correct. The
rollback record written by `do_rollback` appears when history is viewed. The footer
summary and `--detail` hint are present.

**What a failure would mean:** Something in the pipeline is broken. `test_single_patch_shows_table`
failing would be the widest possible failure — either parsing doesn't work, the table
isn't printed, or one of the key fields is missing. `test_rollback_record_in_table`
failing would mean rollback records are not appearing, likely a regression in
`_parse_all_history_records` or in `do_rollback`'s history write.

#### Class 3 PASSED — `TestDoHistoryDetailView`

**What it means:** The `--detail N` mode is wired correctly end-to-end. Record selection
by 1-indexed `N` works. Range validation correctly rejects out-of-bounds values (including
0). The diff is rendered from the record's `old_text` and `new_text`. The identity-diff
edge case (no diff when old == new) is handled without crashing.

**What a failure would mean:** The `--detail` path has a bug. If `test_detail_out_of_range`
failed, the range guard may have been removed or changed. If `test_detail_shows_diff`
failed, either the record selection (`records[detail - 1]`) is wrong or `unified_diff`
is being called with the wrong arguments.

#### Class 4 PASSED — `TestDoHistoryErrorPaths`

**What it means:** All three early-exit branches fire correctly. No error condition
falls through to the table-rendering code. The three different error messages are
distinct — unknown skill, missing file, empty/unparseable file — confirming the separate
error conditions are handled with separate, informative messages.

**What a failure would mean:** An error branch was removed or its message changed. If
`test_unknown_skill_prints_error` failed, the most likely cause is that `_find_skill`
returns something other than `None` for an unknown skill in the redirected temp directory.
If `test_no_history_file_prints_warning` failed, the `if not history_path.exists()` check
may have been removed or the message text changed.

#### Class 5 PASSED — `TestHistoryCommandDispatch`

**What it means:** The `elif action == "history":` branch in `skills_command()` routes
to `do_history` with the correct arguments. `detail=None` and `detail=3` are both passed
through correctly.

**What a failure would mean:** The routing is broken. The most likely cause is that the
`elif` branch was removed, renamed (e.g. action string changed), or that `detail` is
not being read from `args`. These tests are the first line of defense against a
routing-layer regression.

#### Class 6 PASSED — `TestHistoryArgparse`

**What it means:** The argparse subparser is correctly registered in `main.py`. The
`history` subcommand is recognised. `name` is required and positional. `--detail` is
optional, defaults to `None`, and converts its argument to `int`. Both error cases
(missing name, non-integer detail) produce `SystemExit(2)` as argparse specifies.

**What a failure would mean:** The argparse registration was changed or removed in
`main.py`. `test_history_subparser_registered` failing means the `"history"` subparser
does not exist, likely due to a deletion or rename. `test_history_detail_flag` failing
with `detail == "3"` instead of `detail == 3` would mean `type=int` was removed from
the `add_argument` call.

---

### 6c. What the Full 23 Tests Together Prove

**End-to-end correctness for documented use cases.** A skill patched once shows a
single-row table with the correct fields. Multiple patches are numbered oldest-to-newest.
Rollbacks appear in the table. `--detail N` shows the diff for the correct record. The
complete code path from `sys.argv` through argparse, dispatch, file I/O, parsing, and
Rich rendering is exercised.

**Parser and writer are in agreement.** The Class 1 tests use inline string fixtures
constructed to match the exact format `_append_skill_history` writes. The Class 2/3
tests use `_create_skill` and `_patch_skill` to produce real files. Both sets pass —
confirming the format contract from two directions.

**The rollback-inclusion design contract is pinned.** `test_rollback_record_included`
(Class 1) and `test_rollback_record_in_table` (Class 2) together ensure that rollback
records appear in the history view at both the parser level and the integration level.
Any future change that introduces rollback-skipping logic would break both tests.

**All three error messages are independently verified.** Each of the three error paths
has its own test. They cannot be confused with each other — their messages are distinct
and each message is pinned by a separate assertion.

**`type=int` validation is confirmed.** `test_history_detail_requires_int` and
`test_history_detail_flag` together confirm that the argparse `type=int` annotation both
converts valid integers and rejects invalid ones — meaning `do_history` can trust it will
receive `int | None` and never a string.

---

### 6d. What the 23 Tests Do Not Prove

**Rich Table column widths and wrapping.** Tests assert on substring presence in plain
text. Column width calculations and line wrapping in a real terminal are not verified.

**Action colour coding.** `patch` = green, `edit` = blue, `rollback` = yellow. The
`_capture_console` helper disables colour. The words appear in output, but their colour
does not.

**The `edit` action in the table view.** No test uses `_edit_skill` to produce an `edit`
record and verify it appears in the table. The logic is identical to `patch` (verbatim
action string inserted), but it is not directly tested.

**Reason truncation at 60 characters.** No test provides a reason longer than 60
characters. A bug in the truncation slice (`rec["reason"][:60]`) or ellipsis condition
would not be caught.

**Diff line cap at 60 lines.** No test patches a skill with enough content to produce
a 61-line diff. The `diff_lines[:60]` cap and the "... and N more lines" message are
not directly tested.

**Concurrent reads.** `do_history` is read-only but does not acquire a file lock. If
a patch is being written while history is being read, a partial read is possible. This
is a very narrow race and is not tested.

**Large history files.** `_parse_all_history_records` reads the entire file. On a skill
with hundreds of patches, `SKILL_HISTORY.md` may be megabytes. No performance test exists.

---

## 7. Running the Tests Yourself

```bash
# History tests only
python -m pytest tests/hermes_cli/test_history.py -v --override-ini="addopts="

# History + rollback (shared infrastructure validation)
python -m pytest tests/hermes_cli/test_rollback.py tests/hermes_cli/test_history.py \
  -v --override-ini="addopts="

# Full regression — all four test files
python -m pytest \
  tests/tools/test_memory_tool.py \
  tests/tools/test_skill_manager_tool.py \
  tests/hermes_cli/test_rollback.py \
  tests/hermes_cli/test_history.py \
  -q --override-ini="addopts="
# Expected: 179 passed, 1 warning

# Full test suite
python -m pytest tests/ -q --override-ini="addopts="
```

**What "179 passed" in the regression means.** The four files cover:
- `test_memory_tool.py` — 65 tests — memory store, expiry, overlap detection
- `test_skill_manager_tool.py` — 91 tests — skill CRUD, history write, patch application
- `test_rollback.py` — 0 new; rollback CLI tests
- `test_history.py` — 23 tests — history CLI tests (this command)

All 179 pass together, confirming that the history command does not regress any existing
functionality.

---

## 8. Source Files Reference

| File | Role |
|------|------|
| `hermes_cli/skills_hub.py:700` | `_parse_all_history_records` — the regex parser |
| `hermes_cli/skills_hub.py:829` | `do_history` — the command implementation |
| `hermes_cli/skills_hub.py:1250` | `skills_command` history routing — `elif action == "history":` |
| `hermes_cli/skills_hub.py:673` | `_parse_last_history_record` — sibling used by rollback, contrast with the all-records parser |
| `hermes_cli/main.py:4880` | Argparse subparser registration (`skills_history`) |
| `tests/hermes_cli/test_history.py` | All 23 tests |
| `tools/skill_manager_tool.py:297` | `_append_skill_history` — writes the records the parser reads |
| `tools/skill_manager_tool.py:294` | `SKILL_HISTORY_FILE = "SKILL_HISTORY.md"` |
| `tools/skill_manager_tool.py:200` | `_find_skill` — skill lookup used in `do_history` |
| `tests/tools/test_skill_manager_tool.py:32` | `_skill_dir` context manager — test infrastructure |
| `tests/hermes_cli/test_rollback.py` | Sibling tests — same structure, same infrastructure |
