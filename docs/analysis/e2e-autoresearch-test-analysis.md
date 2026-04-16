# E2E Autoresearch Tests — Coverage, Realism, and Limits

> This document analyses `tests/integration/test_autoresearch_e2e.py`.
> It answers four questions: *what the tests cover*, *what they don't cover*,
> *how close they are to reality*, and *whether they stress the autoresearch
> feature's actual usefulness*.

---

## 1. What the Tests Cover

Four tests across three classes:

| Test | Stage(s) exercised | Real I/O? |
|------|-------------------|-----------|
| `TestStage1RealData::test_extracts_signals_from_real_db` | Stage 1 only | Real SQLite read/write |
| `TestStage3RealSkill::test_accepted_patch_modifies_skill_md` | Stage 3 only | Real file read/write |
| `TestStage3RealSkill::test_dry_run_leaves_skill_md_unchanged` | Stage 3 only | Real file read (no write) |
| `TestFullLoop::test_full_pipeline_improves_skill` | Stage 1 → 2 → 3 | Real SQLite + real files; LLM stubbed |

### What passes through real code

- **Stage 1 signal extractor** — reads the sessions and messages tables from a real SQLite
  file, identifies correction-bearing messages, and maps sessions to skills by scanning
  `hermes_home/skills/*/`.
- **Stage 1 metrics writer** — aggregates per-skill health metrics and writes them to
  `skill_metrics.db` using the live `upsert_skill_health()` path.
- **Stage 1 reporter** — generates a real `nightly_report.md` from the metrics.
- **Stage 2 anomaly detector** — reads `skill_metrics.db` and applies the real thresholds
  (`correction_rate > 0.30`, `invocation_count >= 3`).
- **Stage 2 hypothesis generator** — sends real prompt templates to the LLM (stubbed) and
  parses the JSON response using the live parsing code.
- **Stage 2 self-play evaluator** — runs real token-counting and score-averaging logic
  against the stub's scripted responses; acceptance decision uses real thresholds.
- **Stage 3 patch applier** — performs real `old_string → new_string` replacement on a
  real file, using atomic tempfile + `os.replace()`.
- **Stage 3 history writer** — appends a real `[autoresearch]` entry to `SKILL_HISTORY.md`.
- **Stage 3 digest formatter** — produces a real markdown digest.
- **`run_full_loop()` orchestration** — real stage chaining, real `state.json` write,
  real `ImportError` / exception handling paths.

---

## 2. What the Tests Do Not Cover

### Missing stage paths

- **Regression watch** — all four tests pass `run_regression_watch=False`. The rollback
  mechanism (the safety net that reverts a bad patch after it worsens metrics) is never
  exercised here. It has its own unit tests in `test_autoresearch_stage3.py`, but this
  e2e file does not include an end-to-end scenario of: apply patch → next night →
  metrics worsen → rollback triggered.

- **Recency lock** — the 24-hour guard that prevents autoresearch from racing with an
  in-session patch is never tested here. No test seeds `SKILL_HISTORY.md` with a recent
  `[in-session]` entry and then runs Stage 3.

- **Stage 2 hold state** — when judge scores disagree heavily, Stage 2 marks a patch
  `"hold"` rather than `"accepted"` or `"rejected"`. No e2e test exercises this path.

- **Stage 2 null hypothesis** — when the LLM concludes the skill is fine and returns
  `{"patch": null}`, Stage 2 skips the skill. No e2e test covers this path.

- **Scheduler tick** — `_tick_autoresearch()` (the entry point called every 60 seconds
  by `tick()`) is not exercised. The tests call `run_full_loop()` directly, bypassing
  the croniter schedule check and the never-ran / already-ran detection logic entirely.

- **Delivery** — `deliver_digest()` is not called in any e2e test. Slack and Telegram
  delivery, env-var resolution, and gateway integration are untested here.

- **Multi-skill scenarios** — all tests use a single skill. No test verifies that Stage 2
  correctly prioritises multiple underperforming skills, or that Stage 3 applies patches
  to multiple skills in one run.

- **Stale patch guard** — if the skill file changed between Stage 2 running and Stage 3
  applying, Stage 3 should detect the old_string is missing and skip. No e2e test covers
  this.

---

## 3. How Close to Reality (What Is Mocked, What Is Not)

### Not mocked

| Component | Reality level |
|-----------|---------------|
| SQLite reads/writes (Stage 1) | Real — same schema as production |
| File I/O for SKILL.md, SKILL_HISTORY.md, state.json | Real — actual disk writes |
| Anomaly detection thresholds | Real — live code, live constants |
| Prompt template construction (Stage 2) | Real — actual prompt strings sent to stub |
| JSON patch parsing (Stage 2) | Real — production parser runs |
| Token delta + score averaging (self-play evaluator) | Real — live arithmetic |
| Stage 3 atomic write | Real — tempfile + os.replace |
| Stage orchestration and error isolation in run_full_loop() | Real — live try/except chains |

### Mocked or scripted

**The LLM (`_make_accepting_llm`)** is the single largest departure from reality. It is a
scripted switch-statement that inspects the last message's content and returns a
hardcoded response:

```python
def _llm(messages):
    last = messages[-1]["content"]
    if "Propose a targeted patch" in last:
        return json.dumps({"patch": {"old_string": OLD_STRING, "new_string": NEW_STRING, ...}})
    if "## Skill" in last:
        return "Short. " * 5 if NEW_STRING in last else "Long response. " * 20
    if "Score the following" in last:
        return "8" if "Short." in last else "6"
    return "5"
```

This stub does four things a real LLM never guarantees:

1. **Always returns valid JSON.** A real LLM may produce malformed JSON, prose instead of
   JSON, or partial responses. The live hypothesis parser's error handling is never
   stressed.

2. **Always returns the exact `OLD_STRING` from the test constants.** A real LLM reads
   the skill file and invents a patch. It may identify a different substring, a substring
   that doesn't exist, or a change so large it fails the old_string search.

3. **Always produces a favourable token delta.** The stub returns a short response for the
   new skill version and a long response for the old. Token delta is therefore always
   negative (improvement). In reality, the new version might produce longer or identical
   responses, causing rejection.

4. **Always produces consistent judge scores (8 vs 6).** Real judge calls may disagree
   (triggering `"hold"`), refuse to produce a number, or score both versions equally.

**The session data** is also unrealistically clean:
- All corrections are the same string: `"try again"`.
- All corrected sessions have exactly one correction message.
- Token counts are uniform (500/500 for every session).
- System prompts use the exact format `"Use the demo-skill skill for this task"`.

Real sessions have varied token counts, multiple corrections with different wording,
richer system prompts, and interleaved tool calls. The signal extractor's correction
pattern matching and skill detection code run correctly, but against idealised inputs
that never probe edge cases.

---

## 4. Do These Tests Stress the Usefulness of Autoresearch? No.

This is the most important honest answer.

### What "useful" would require

For autoresearch to be genuinely useful, it must answer the question: **does the skill
work better after the patch?** That means:

1. The LLM identifies a real flaw in a real skill based on real usage patterns.
2. The LLM proposes a patch that addresses the flaw, not just any change.
3. After the patch is applied, the skill performs measurably better on real tasks.

None of these three things are tested.

### Why the full-loop test is circular

The e2e test seeds a `SKILL.md` containing `OLD_STRING = "Always do the right thing."`.
The stub LLM is hardcoded to return a patch from `OLD_STRING` to `NEW_STRING`.
The test then asserts `NEW_STRING in content`.

This proves: **bytes flowed from the stub through the pipeline to the file**. It does not
prove that `NEW_STRING` is a better rule than `OLD_STRING`, or that the LLM would have
identified this particular flaw on a real skill. The "improvement" is written by the
test author, not discovered by the AI.

### The self-play evaluator is evaluated against itself

The self-play evaluator's job is to determine whether the new skill version produces
better agent outputs than the old version. In production, this would require running
actual Hermes agent tasks under each version and comparing quality.

In the e2e test, the stub LLM plays all three roles simultaneously — it is the agent
running tasks under the old skill, the agent running tasks under the new skill, *and*
the judge scoring both versions. Each role returns a scripted value. The evaluator's
score-averaging logic runs, but on inputs that are designed to produce a predetermined
outcome. The test proves the evaluator can add up scores correctly, not that the scores
reflect reality.

### What would actually stress usefulness

**Option A — Golden answer test (feasible without live LLM).** Take a real Hermes skill
file with a known documented flaw. Write a test that seeds sessions reflecting that flaw
(specific patterns of corrections, specific failure modes). Run Stage 1 and verify the
correct flaw is detected. Then hand-write a known-good patch and verify Stage 3 applies
it correctly. This keeps the LLM stub but ties the scenario to a real skill and a real
flaw.

**Option B — Retrospective replay test (requires archived data).** Collect real
`state.db` data from a period when a skill was known to be underperforming (e.g.,
recorded corrections from actual Hermes sessions). Run the full pipeline with a real LLM.
Evaluate whether the generated patch addresses the observed failure mode. This is a true
regression-detection benchmark.

**Option C — Behavioural before/after test (requires live LLM + agent).** Run a set of
benchmark tasks against a skill in its original state. Apply an autoresearch patch. Re-run
the same benchmark tasks. Measure whether error rate, correction rate, or token cost
improved. This is the only test that answers the usefulness question directly.

All three options are significantly harder than the current e2e tests. Option A is the
most achievable in the near term without a live LLM.

---

## 5. Summary

| Question | Answer |
|----------|--------|
| Does real code run? | Yes — all stage components except the LLM |
| Does real data flow? | Yes — real SQLite, real files, real disk writes |
| Is the LLM real? | No — scripted stub with predetermined outputs |
| Does the stub expose LLM failure modes? | No — it never fails or produces bad output |
| Is the session data realistic? | No — idealised, uniform, no edge cases |
| Is regression watch tested? | No — disabled in all four tests |
| Is the scheduler path tested? | No — direct `run_full_loop()` call |
| Is delivery tested? | No — not called |
| Do the tests prove autoresearch is useful? | No — the "improvement" is circular |
| What would prove usefulness? | A golden-answer or retrospective-replay test against a real skill with a documented flaw |
