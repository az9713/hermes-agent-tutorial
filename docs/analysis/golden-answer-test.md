# Golden-Answer Test — Reference and Honest Assessment

> This document is the complete reference for
> `tests/cron/test_autoresearch_golden_answer.py`.
> It answers: *why this test exists*, *what is real vs. mocked*,
> *what the golden assertion proves*, and *what it still does not cover*.

---

## 1. Why This Test Exists

The four e2e tests in `tests/integration/test_autoresearch_e2e.py` prove that
data flows correctly through the autoresearch pipeline. They do not prove that
the AI judgment is sound. The full-loop e2e test is circular:

- The LLM stub is pre-programmed with the exact `old_string` and `new_string`.
- The test then asserts those exact strings appear in the output.
- The "improvement" was written by the test author, not discovered by the AI.

The golden-answer test breaks the circularity. It gives Claude a synthetic skill
file containing a deliberately bad instruction and asks the AI to identify and
propose a fix — without being told what the fix should be. The assertion is
semantic: does the proposed patch target the bad instruction?

**This is the only test in the suite that actually stresses AI judgment.**

---

## 2. The Documented Scenario

**Skill:** `safe-git-push` — a synthetic skill that instructs the agent to commit
and push code changes.

**Documented flaw:**

```
3. Push directly to main: `git push origin main`
```

Pushing directly to main without checking the active branch is unambiguous bad
practice. Any competent reviewer reading this skill should flag it.

**Why "branch" is absent from the original content:**

The word "branch" does not appear anywhere in the original `SKILL_CONTENT`. This
is intentional. If the patched content contains "branch", it proves the fix is
branch-aware — the AI introduced the concept, not the test author.

**Session data:**

Five sessions are seeded, each invoking `safe-git-push` and each containing a
user correction message that matches Stage 1's `_CORRECTION_PATTERNS`:

| Session | Correction message |
|---------|-------------------|
| s-0 | `"that's wrong, you pushed to main directly"` |
| s-1 | `"try again — never push to main without checking the branch"` |
| s-2 | `"that's incorrect, you need to be on a feature branch first"` |
| s-3 | `"start over, you should not push to main like that"` |
| s-4 | `"that didn't work, always verify the branch before pushing"` |

Stage 1 detects a 100% correction rate → flags `safe-git-push` as underperforming.

**What Claude actually sees:**

The hypothesis generator sends Stage 2 the full SKILL.md content and the anomaly
metrics (correction_rate, completion_rate, invocation_count). It does **not** send
the raw correction message text — only `"Session s-N: 1 correction(s)"` as
session excerpts. Claude must identify the flaw from the skill content alone.

---

## 3. What Runs Real Code vs. What Is Mocked

| Component | Real or Mocked |
|-----------|---------------|
| Stage 1 signal extractor | **Real** — reads real SQLite, applies real patterns |
| Stage 1 metrics writer | **Real** — writes real `skill_metrics.db` |
| Stage 1 reporter | **Real** — writes real `nightly_report.md` |
| Stage 2 anomaly detector | **Real** — live threshold checks |
| Stage 2 hypothesis generator | **Real Claude API call** |
| Stage 2 self-play evaluator | **Real Claude API calls** (multiple) |
| Stage 3 patch applier | **Real** — writes real `SKILL.md` (if patch accepted) |
| Session data | Synthetic but schema-accurate |

Nothing in Stage 2 is stubbed. The hypothesis generation, rephrase calls, agent
simulation calls, and judge calls all go to the real Claude API via
`agent.auxiliary_client.call_llm`.

---

## 4. The Golden Assertion

```python
assert BAD_INSTRUCTION in candidate["old_string"]
assert BAD_INSTRUCTION not in candidate["new_string"]
```

where `BAD_INSTRUCTION = "Push directly to main: \`git push origin main\`"`.

**Why this is non-circular:**

The test author defines what counts as a flaw (`BAD_INSTRUCTION`) and what counts
as a fix (not containing `BAD_INSTRUCTION`). But the test does **not** prescribe
the replacement wording. Claude must:

1. Read the skill content.
2. Identify `BAD_INSTRUCTION` as the problematic substring.
3. Propose any replacement that does not repeat the instruction.

If Claude instead identifies a different substring (e.g., a note in the `## Notes`
section), the first assertion fails — Claude missed the documented flaw.

If Claude proposes a replacement that still says "Push directly to main", the
second assertion fails — Claude's fix is not a fix.

Neither failure is possible with a scripted stub, because the stub always returns
the exact strings the test expects. With real Claude, both are genuine failure modes.

**What the assertion does not require:**

The test does not require the patch to be accepted by the self-play evaluator, and
does not require specific wording in `new_string`. Claude may use any phrasing that
removes the bad instruction — "verify you are on a feature branch", "never push to
main directly", "check `git branch` first", etc.

---

## 5. What the Test Does Not Cover

**Self-play acceptance is informational, not required.**

Stage 3 only runs if the patch is accepted. If the self-play evaluator rejects
(e.g., the new wording produces longer agent responses → token delta positive →
rejected), the golden assertion has already passed and the test does not fail. The
rejection reason is printed for information.

This is an honest limitation: the test proves Claude can *identify* the flaw and
*propose* a fix, but not that the full pipeline will *apply* the fix. The self-play
acceptance decision depends on real LLM variance across calls.

**Still not covered:**

- Regression watch — all calls use `run_regression_watch=False`
- Recency lock
- Scheduler tick (`_tick_autoresearch()`)
- Delivery (Slack, Telegram)
- Multi-skill scenarios

---

## 6. Pass / Skip / Fail Semantics

| Outcome | What it means |
|---------|--------------|
| **PASS** | Real Claude identified `BAD_INSTRUCTION` as the old_string and proposed a replacement that does not contain it. The AI judgment is working. |
| **SKIP** | The LLM client is not configured or the probe call failed (wrong model, no API key, network error). The test cannot run in this environment. This is not a test failure — it is an environment limitation. |
| **FAIL** | The LLM client is reachable but Claude did not identify the documented flaw. Either (a) Claude proposed a patch targeting a different substring, or (b) Claude said no patch was possible, or (c) Claude proposed a fix that still contains the bad instruction. This is a genuine failure of AI judgment. |

The distinction between SKIP and FAIL matters. A SKIP means "we can't test this
here". A FAIL means "we tested it and the AI got it wrong".

---

## 7. How to Run

The test is excluded from the default pytest run
(`addopts = "-m 'not integration'"` in `pyproject.toml`).
Run it explicitly:

```bash
pytest tests/cron/test_autoresearch_golden_answer.py \
       --override-ini="addopts=" -v -s
```

Expected output when the LLM is configured and working:

```
PASSED  — Claude identified the bad instruction and proposed a fix
```

or

```
SKIPPED — LLM client not usable in this environment — BadRequestError: ...
```

To confirm Stage 1 ran correctly before Stage 2, run with `-s` to see log output.
