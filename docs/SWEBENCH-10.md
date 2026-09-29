# Fixed ten-issue SWE-bench Lite evaluation

This is a small, reproducible product check, not an industry ranking. The
selection, all ten predictions, the submitted and unfiltered patches, local run
reports, and official grader reports are retained in this repository.

## Protocol

- Dataset: `SWE-bench/SWE-bench_Lite`, `test` split, pinned revision
  `b0dde1093fe417d83b7184254edf8199c1f0dff5`.
- Selection: sort repository names, take the first ten distinct repositories,
  then choose the issue with the smallest SHA-256 hash of `instance_id` within
  each. The [manifest](../benchmarks/swebench/manifest-test-10.json) was written
  before the attempts. Each selected issue received one model attempt.
- Agent source: commit `4d4eaf99fd8d7dc7277ebb8400ad5d555eaece04`.
  Provider/model: DeepSeek `deepseek-flash`. Limit: 48 tool calls per issue.
- Each task started from its recorded base commit. The agent saw an isolated
  repository copy with a synthetic one-commit baseline, so later Git history
  was not available. Commands ran in the corresponding prebuilt official
  SWE-bench task image, with network disabled and only the task copy mounted.
  Credentials stayed in the host process. The task images and dependencies
  were prepared before inference.
- Prediction patches exclude agent-written test files; the unfiltered patches
  and excluded paths remain in the [raw records](../benchmarks/results/swebench-lite-10-4d4eaf9/attempts.jsonl).
  Empty predictions and preparation errors before inference are retained.
- Grader: official `swebench` 5.0.2 harness, one worker, run ID
  `cobalt-test-10-4d4eaf9`. The [official summary](../benchmarks/results/swebench-lite-10-4d4eaf9/official-summary.json)
  reports ten submitted IDs, six nonempty patches evaluated, and four empty
  patches. It reports no infrastructure, ambiguous, or patch application errors.

## Results

| Instance | Agent stop | Submitted patch | Official result |
| --- | --- | ---: | --- |
| `astropy__astropy-6938` | Tool limit | 565 B | Resolved |
| `django__django-15202` | Tool limit | 2,086 B | Resolved |
| `matplotlib__matplotlib-25433` | Tool limit | Empty | Unresolved |
| `mwaskom__seaborn-3010` | Completed | 424 B | Resolved |
| `pallets__flask-5063` | Tool limit | 2,819 B | Unresolved |
| `psf__requests-2674` | Tool limit | Empty | Unresolved |
| `pydata__xarray-4248` | Completed | Empty | Unresolved |
| `pylint-dev__pylint-7114` | Tool limit | 144 B | Unresolved |
| `pytest-dev__pytest-9359` | Tool limit | 1,513 B | Resolved |
| `scikit-learn__scikit-learn-25747` | Tool limit | Empty | Unresolved |

**Official resolved: 4/10, across four repositories.** The internal release
gate of at least 3/10 across at least two repositories with no observed
isolation failure was met. The ten issues are too few to estimate general
success across repositories. `completed` means the agent stopped normally;
the Xarray run shows that this alone does not imply a patch or a solved issue.
Conversely, three resolved issues ended at the tool-call limit and were graded
from their working-tree patches.

The [Flask instance report](../benchmarks/results/swebench-lite-10-4d4eaf9/official-reports/pallets__flask-5063.json)
shows an applied patch with both required `FAIL_TO_PASS` tests still failing.
The [Pylint report](../benchmarks/results/swebench-lite-10-4d4eaf9/official-reports/pylint-dev__pylint-7114.json)
also shows an applied patch with its required test still failing. These are
task failures, not grader failures. The attempt records show 4,933,605 prompt
tokens and 131,684 completion tokens in aggregate, measured as reported by the
model API; this includes repeated context across calls and should not be read
as unique task content.

## Reproduce the grading

Install the official SWE-bench harness and make the selected task images
available to Docker. Then run from the repository root:

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name SWE-bench/SWE-bench_Lite --split test \
  --instance_ids astropy__astropy-6938 django__django-15202 \
  matplotlib__matplotlib-25433 mwaskom__seaborn-3010 \
  pallets__flask-5063 psf__requests-2674 pydata__xarray-4248 \
  pylint-dev__pylint-7114 pytest-dev__pytest-9359 \
  scikit-learn__scikit-learn-25747 \
  --predictions_path benchmarks/results/swebench-lite-10-4d4eaf9/predictions.jsonl \
  --max_workers 1 --run_id cobalt-test-10-4d4eaf9-recheck
```

The prediction file SHA-256 is
`8113976d529f0f426d86d8321aa99259503ff16e44a62321a8251a743fb8ce0f`.
See the [official evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)
for scorer setup and report interpretation.
