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
   successful commands, answer-source audit, elapsed time, and
   provider-reported token use. A repair may pass its external verifier while
   its explanation is `unverified` because it cites code that changed after
   the last read. Both facts are reported separately.

Run the local checks:

```powershell
python -m unittest discover -s tests -v
ruff check src tests scripts benchmarks/fixtures
```

Run the live set after configuring `DEEPSEEK_API_KEY`:

```powershell
python scripts/evaluate_live.py
```

Results appear as dated JSON files in `benchmarks/results/`. The fixture code,
task prompts, and external verifiers are in `benchmarks/` so each result can be
inspected and repeated. These small tasks are a smoke benchmark, not a claim
about general coding success. Each live row also records individual tool
outcomes and a failure category. The answer grader for repository questions
checks that a file was read and required facts appear; it is a lightweight
task-specific check, not a general factuality judge.
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
