# Evaluation

We report three different claims separately:

1. **Runtime contract:** local tests check path isolation, stale-write
   rejection, read-only mode, tool feedback, session resume, and evidence
   labeling. A scripted model makes these checks deterministic.
2. **Task outcome:** a live model attempts each task in a fresh fixture copy.
   For repair cases, an external command checks the final repository. For
   question cases, the evaluator requires a real read and expected answer
   terms.
3. **Process evidence:** each row records the agent's status, tool calls,
   successful commands, elapsed time, and provider-reported token use.

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
about general coding success. The repository will add harder tasks and failure
categories before using a success rate in a resume.

## First live baseline

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
