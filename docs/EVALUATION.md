# Evaluation

We report three different claims separately:

1. **Runtime contract:** local tests check path isolation, stale-write
   rejection, read-only mode, tool feedback, session resume, and evidence
   labeling. A scripted model makes these checks deterministic.
2. **Task outcome:** a live model attempts each task in a fresh fixture copy.
   For repair cases, tests outside the agent's workspace check the final code,
   and visible tests must remain unchanged. The external tests must fail before
   the agent starts. For question cases, the evaluator requires a real read and
   expected answer terms.
3. **Process evidence:** each row records the agent's status, tool calls,
   successful commands, post-edit reads, answer-source audit, elapsed time, and
   provider-reported token use. A repair may pass its external verifier while
   its explanation is `unverified` because it cites code that changed after
   the last read. Both facts are reported separately.

Run the local checks:

```powershell
python -m unittest discover -s tests -v
ruff check src tests scripts benchmarks/fixtures
```

Run the live set after filling a local `.env` (or setting the selected
provider's key in the process environment):

```powershell
python scripts/evaluate_live.py
# Optional: repeat each fresh fixture independently to observe model variance.
python scripts/evaluate_live.py --repeat 3
```

Results appear as dated JSON files in `benchmarks/results/`. The fixture code,
task prompts, and external verifiers are in `benchmarks/` so each result can be
inspected and repeated. These small tasks are a smoke benchmark, not a claim
about general coding success. Each live row also records individual tool
outcomes and a failure category. The answer grader for repository questions
checks that a file was read and required facts appear; it is a lightweight
task-specific check, not a general factuality judge.
The source-audit count includes an answer only when it contains at least one
explicit `file:line` location and none of those locations is unsupported.
The live runner refuses an uncommitted working tree by default so a report's
commit points to the code that produced it. `--allow-dirty` is available for
exploration and marks the report accordingly.

## First live baseline (visible verifier protocol)

On commit `857eb9676953d9ebc341472cbf1be7af38abd8c0`, `deepseek-flash`
passed all three fresh-workspace smoke cases. The two repair cases also passed
their external `unittest` verifiers, and the agent ran one successful command
after each edit. The source data is in
`benchmarks/results/live-20260923-113522.json`.

| Case | Result | Tool calls | Seconds | Prompt tokens | Completion tokens |
| --- | --- | ---: | ---: | ---: | ---: |
| Locate entrypoint | pass | 3 | 3.17 | 3,809 | 350 |
| Fix invoice total | pass | 6 | 7.70 | 11,981 | 906 |
| Fix empty average | pass | 5 | 5.88 | 8,069 | 674 |

This is a three-case smoke run, with one attempt per case. It is useful for
verifying the complete model-to-tool-to-verifier path, but too small and simple
to estimate success on real issue reports.

## Expanded live run (visible verifier protocol)

On commit `3c2ba47d4e336282227e17fd5e65410fe6c8baa9`, `deepseek-flash`
passed all six cases in one attempt per case. Four repairs passed an external
`unittest` command in fresh copies, and two repository questions used actual
file reads. The new cases include precedence across modules, a cache
invalidation defect across three modules, and untrusted instructions embedded
in repository text. The source data is
`benchmarks/results/live-20260923-115552.json`.

| Case | Result | Tool calls | Seconds | Prompt tokens | Completion tokens |
| --- | --- | ---: | ---: | ---: | ---: |
| Locate entrypoint | pass | 3 | 2.58 | 2,545 | 366 |
| Fix invoice total | pass | 4 | 4.56 | 6,310 | 452 |
| Fix empty average | pass | 5 | 5.94 | 8,229 | 709 |
| Fix config precedence | pass | 6 | 5.49 | 9,989 | 717 |
| Ignore repository instruction | pass | 2 | 1.92 | 2,457 | 212 |
| Fix cache invalidation | pass | 6 | 5.39 | 9,905 | 815 |

These tasks establish a repeatable end-to-end path and expose the raw action
record. Six short cases and a single run per case cannot estimate success on
unseen repositories. The instruction-resistance case checks whether the model
answered the user's code question, not every possible prompt-injection attack.

## External-verifier and source-audit run

On commit `445dfaf6d4d9189ce064e35af922d698cd62c30a`, the six tasks
passed again in one attempt each. The four repair cases used test files held
outside the agent workspace. Each external verifier failed on the initial
fixture and passed on the final code; none of the visible test files changed.
The two question cases were answered after file reads. The complete record is
`benchmarks/results/live-20260923-122715.json`.

| Measurement | Result | What it means |
| --- | ---: | --- |
| Task outcome | 6/6 | Four hidden repair verifiers passed; two question checks passed. |
| Repair baseline failures | 4/4 | Each hidden verifier detected a defect before the run. |
| Visible tests unchanged | 4/4 | Repair cases did not weaken their supplied tests. |
| Answer source audit | 2/6 | Four repair explanations cited changed files without a fresh read. |
| Prompt / completion tokens | 59,201 / 4,542 | Provider-reported totals for this single run. |

The last row of failures is intentional to surface: a correct patch and a
well-grounded explanation are different outcomes. The source audit checks
explicit `file:line` locations against current file digests and lines read in
that run. It does not prove the prose around a valid citation, and it cannot
detect an unsupported claim that has no explicit location. The benchmark is
still six short cases, so these counts are diagnostic, not an estimated
success rate for unseen issue reports.

## Post-edit read run

On commit `7b61dc3b3f6ecac15b67243ea8e4868494fa0d0a`, the runtime required
each changed file to be reread before a final answer. In this single six-case
run, all tasks passed, the four repair tasks completed their post-edit reads,
and all six final answers had supported explicit source locations. Seventeen
`file:line` references were checked. The raw record is
`benchmarks/results/live-20260923-172150.json`.

| Measurement | Prior run | This run |
| --- | ---: | ---: |
| Task outcome | 6/6 | 6/6 |
| Answers without unsupported locations | 2/6 | 6/6 |
| Explicit locations checked | Not recorded | 17 |
| Prompt / completion tokens | 59,201 / 4,542 | 53,660 / 4,519 |

The two runs used the same six task definitions and model but were single
samples. Token counts and model behavior can vary between attempts; this table
does not establish a causal performance gain. The deterministic contract tests
verify that a final answer after a file edit is rejected until a successful
fresh read, or marked `unverified` if the model ignores the correction.

## Context pressure and evidence visibility

The three tests in `tests/test_engine.py` exercise `Agent.ask` with real file
reads and commands, using a scripted model so the runtime behavior is
repeatable. Each test first failed against commit `f54e3ae`, then passed after
its corresponding change. These are runtime contract tests, not DeepSeek task
success measurements.

| Pressure case | Observed failure before change | Result after change |
| --- | --- | --- |
| Six large file reads in one user turn | Next model request was 75,743 characters against a 48,000-character budget. | Request was 39,605 characters; three older read results were marked as omitted in the model view, while all six complete results stayed in the session. |
| Five large command outputs | The runtime sent another over-budget model request even though command outputs might describe effects that should not be repeated. | The run returned `context_limit` before another model call; the outputs remained in the session. |
| Citation to an omitted read | `large.txt:1` passed the source audit even though that read result was absent from the last model request. | The citation was marked unsupported, and the final answer stayed `unverified` when the scripted model repeated it. |

The first case preserves tool call/result pairing and records omitted call IDs
in `context_built`. At the time of that baseline, the second case stopped
explicitly. The third case checks what the model could actually see rather than
every read that happened earlier in the run. The 48,000-character limit is a
local heuristic; it does not equal the provider's token limit. A real-model
evaluation must measure task success, retries, latency, and token use after
this view transformation.

## Fault-injection iteration: command output and recovery

These checks exercise `Agent.ask` and session resume with a scripted model and
real local tool effects. Each new test was run against the preceding behavior
before the corresponding change. The failures below are observed outcomes,
not estimates of how often a live model fails.

| Fault | Before | After | Contract checked |
| --- | --- | --- | --- |
| Five commands each print about 12,000 characters | `context_limit`; no second model request | `completed`; second request fits 48,000 characters | Old successful output is omitted only from the model view, with exit code, length and digest retained. The full output stays in the session; each command executes once. |
| Command increments a file, then the process stops before recording its reply | Immediate model retry increments it again: `1 → 2` | Retry denied; file remains `1` | Effectful tools wait for a successful inspection that the model can see. |
| Model requests inspection and a write in the same batch | Write executes before the model sees inspection: `1 → 2` | Write denied; file remains `1` | A tool result cannot authorize another call from the same model response. |
| Process stops again after inspection was stored, but the next model context omits it | Recovery barrier cleared from transcript alone | Write remains denied | Resolution depends on what the model request actually contained, not merely what the session stored. |

The recovery barrier is persisted separately from the transcript, and run
events record denied actions and omitted outputs. It prevents blind replay; it
does not prove that a later inspection covers every possible effect of an
arbitrary external command. The command still requires the normal tool gate
approval when it is allowed again.

## Live pressure case: verbose checks

`run_noisy_checks` makes the model read a checker, then execute five independent
stages whose logs are long enough to pressure the context budget. The grader
requires exactly one command attempt with a successful result for each stage and all stage names in
the answer. It also records how many read and command outputs were omitted in
the model view. This case probes a protocol path; it is not a general coding
ability benchmark.

On commit `181de5007746ef3f6d2c2be9bc5f1fa7b44de221`, three independent
DeepSeek attempts passed **2/3**. The failed run executed all five stages
successfully but then called `read_file` and `search` repeatedly, reached the
12-call limit, and gave no final result. Across its model requests, the run
record counts 6 command-output elisions and 5 read-output elisions (the same
tool result may be counted again in a later request). The two passing attempts
also had output elisions. The raw report is
`benchmarks/results/live-20260924-081850.json`.

The failure is consistent with the model trying to recover the `PASS` banners
that the old omission marker had removed, even though it retained exit codes.
That is a diagnostic hypothesis, not proof of the model's private reasoning.
The next revision preserves explicitly labeled raw prefix and suffix excerpts
of old successful logs while still retaining the full result in the session.

On commit `0636fd51860714cfa684e3368c8d2698eaa9ab48`, the same case passed
**4/5** attempts. The failed attempt again ran all five commands successfully,
then repeatedly read and searched `check.py` until reaching the tool limit.
Across its model requests, 10 read-output elisions were recorded. The raw
report is `benchmarks/results/live-20260924-082204.json`.

The next test reproduced the competing-budget problem without a live model:
the previous policy omitted a small `check.py` read before shrinking much larger
successful logs. The revised policy removes old verbose command bodies first,
keeping the short source read visible when that suffices to fit the request.
This is a retention priority, not a claim that the log preview alone caused
the change from 2/3 to 4/5; those are small independent samples.

On commit `436832704439e6fdc07ecbad96918712668b6851`, five fresh attempts
passed **5/5**. Each run used 6–7 tool calls, included 2–3 command-output
elisions, and kept all file-read outputs visible. The largest model request was
42,959 characters, below the 48,000-character policy. The complete record is
`benchmarks/results/live-20260924-082554.json`. These observations support the
retention change for this workload; five attempts are too few to estimate a
general success probability or isolate model randomness from the code change.

## Seven-case regression after the workflow grader fix

On clean commit `8d985b1d36be027cfb8888ade63e0d9c2424270b`, a single
DeepSeek `deepseek-flash` attempt on each of the seven cases passed **7/7**.
The four repair cases passed their external verifiers. All seven answers had
supported source references (16 checked references in total). The verbose
workflow used six tool calls, omitted two older command outputs from model
requests, omitted no file reads, and stayed below the 48,000-character policy
at 42,557 characters. The run used 56,978 prompt tokens and 4,944 completion
tokens in total. Raw observations are in
`benchmarks/results/live-20260924-083049.json`.

The workflow grader now checks every command attempt for each required stage:
exactly one attempt must exist and it must succeed. A scripted failed attempt
followed by a successful retry exposed the earlier false positive. This
seven-case run is a regression check, not a broad success-rate estimate.

## Saved output retrieval protocol

Two additional cases generate a random token during a single command and bury
it beyond the 12,000-character preview. One exits successfully; the other puts
the token in stderr and exits with code 7. An independent receipt records the
generated token and execution count. Passing requires one exact command
attempt, one side effect, unchanged fixture code, a successful read of that
command's archive containing the token, and an answer with the token and exit
code. The token cannot be known from source code alone.

`--without-output-retrieval` removes the read tool from the schema and denies
attempted calls. This is a capability ablation on the same implementation,
model and prompts, not a comparison against an earlier commit. Both arms still
save outputs. Run each case with and without the flag and record independent
attempts with `--repeat`.

Deterministic regressions cover middle-of-output recall after restart, bounded
pagination, search across a 64-KiB chunk boundary, stderr after large stdout,
timeout output, corruption rejection, and context pressure from repeated reads
and failures. A parent process also terminates a real worker before the command
effect, after the effect but before the reply is saved, and after reply saving.
The first two retain a recovery barrier; archive reads alone cannot clear it.
This tests process termination at signaled boundaries, not power loss or every
possible scheduling interleaving.

On clean commit `b4202c3478fe1e161c81a4233ceb79562f90a61d`, DeepSeek
`deepseek-flash` ran each retrieval case three times per arm:

| Read tool | Passed | Command effects | Tool calls | Prompt tokens | Completion tokens | Sum of task seconds |
| --- | --- | --- | --- | --- | --- | --- |
| Disabled | 0/6 | 6 | 43 | 106,042 | 16,379 | 96.203 |
| Enabled | 6/6 | 6 | 20 | 59,056 | 4,622 | 36.609 |

The disabled arm reported inability to recover the random token; it did not
repeat the effectful command. The enabled arm used one archive read per attempt,
including the nonzero-exit stderr case. Both arms started on the same clean
commit and ran concurrently; timing is observational and may include service
load effects. Reports are `benchmarks/results/live-20260924-084909.json`
(disabled) and `benchmarks/results/live-20260924-084809.json` (enabled).
These deliberately retrieval-dependent tasks show capability availability,
not broad coding quality or an estimated general success probability.

Passing validates the specified token/effect contract, not every sentence of
the model's answer. One passing answer said no files were modified even though
the command wrote its receipt; source-reference checks do not establish semantic
truth. Subsequent deterministic checks also found two boundary errors: archive
publication failure hid an already-completed command's exit code, and grading
from a journal excerpt rejected valid tokens later in a retrieved page. The
runtime now reports the actual command exit alongside an archive warning; the
grader inspects complete tool replies rather than journal excerpts.

The final clean-commit regression on
`a4c820ba6c5e283ba28a8b5c119242ed3fd872f9` passed **9/9** cases, including
the original seven and both retrieval cases. Four repair verifiers exited
successfully; each retrieval task had one command effect and one archive read.
The run used 84,738 prompt tokens and 7,617 completion tokens. See
`benchmarks/results/live-20260924-085337.json`. Local verification ran 57 tests:
56 passed and one Windows symlink-permission test was skipped. Static checks
passed. No general success-rate claim follows from this single regression.

## Verification-version boundaries

[VERIFICATION.md](VERIFICATION.md) defines the observable file scope and the
pass/fail conditions for invalidating command evidence. Tests use actual file
operations and subprocesses with scripted model actions, including a concurrent
write injected after a read. A positive edit/check/reread case ensures the
runtime does not satisfy negative cases by rejecting every completion.

`fix_invoice_via_command` extends live evaluation with a repair performed through
`run_command`. The case forbids direct edit tools, requires runtime completion,
and uses an independent verifier. Case-level `forbidden_tools` and
`require_completed` policies are checked by the evaluator, not only requested
in the prompt. Results also record the verification fingerprint and observation
errors.

On clean commit `df815662a3b2b331c2f767ce231906791224c11b`, DeepSeek
`deepseek-flash` passed **10/10** live cases in one regression run. The new
command-edit repair used five tools: two reads, the modifying command, a fresh
read, and a subsequent test command. The result recorded `invoice.py` as changed,
one successful command supporting its verification fingerprint, no observation
errors, and an independently passing verifier. The modifying command itself
was not included as successful verification evidence.

The run used 112,333 prompt tokens and 8,688 completion tokens; raw observations
are in `benchmarks/results/live-20260924-094214.json`. Local verification ran
67 tests: 66 passed and one Windows symlink-permission test was skipped. Static
checks passed. This single live regression does not estimate general task
success, large-repository performance, or correctness under arbitrary concurrent
writers.
