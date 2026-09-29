# Public issue evaluation

This is one fixed attempt on the `test` split of
[`SWE-bench/SWE-bench_Lite`](https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite),
graded with the [official SWE-bench harness](https://www.swebench.com/SWE-bench/guides/evaluation/).
It is a diagnostic example, not a benchmark score or an estimate of success
across repositories.

## Protocol

- Dataset row: `pallets__flask-4045`, the first Flask row in the dataset's
  displayed order. Repository: `pallets/flask`; base commit:
  `d8c37f43724cd9fb0870f77877b7c4c7e38a19e0`.
- Agent source commit: `859fe5378bca25e3eb338dd0f81ea50c1c7a64c1`.
  Model: `deepseek-flash` through DeepSeek. One attempt, 32 tool calls maximum.
- The workspace was made from an archive of the base commit, then initialized
  as a new one-commit Git repository. This prevents later upstream commits from
  appearing in `git log` while allowing `git diff` to produce a prediction.
  An earlier trial against a full clone exposed later history and was discarded
  before grading.
- The issue text alone was given to the agent. The official test patch and
  reference fix were not present in its workspace. The agent stopped at its
  tool-call limit; the working-tree diff was submitted as-is, without manual
  changes or a second candidate.
- The grader was `swebench` 5.0.2 on Linux with Docker, dataset
  `SWE-bench/SWE-bench_Lite`, split `test`, one worker, and run ID
  `cobalt-20260929-flask-4045`.

The exact submitted patch is in
[`swebench-lite-flask-4045-prediction.jsonl`](../benchmarks/results/swebench-lite-flask-4045-prediction.jsonl).
The [official summary](../benchmarks/results/swebench-lite-flask-4045-official-summary.json)
and [instance report](../benchmarks/results/swebench-lite-flask-4045-official-instance.json)
are retained alongside it. To rerun the official grader with an available
Docker engine and the `swebench` package:

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name SWE-bench/SWE-bench_Lite --split test \
  --instance_ids pallets__flask-4045 \
  --predictions_path benchmarks/results/swebench-lite-flask-4045-prediction.jsonl \
  --max_workers 1 --run_id cobalt-20260929-flask-4045-rerun
```

## Result

| Measurement | Observation |
| --- | --- |
| Official resolved | **0/1** |
| Patch applied | Yes |
| `FAIL_TO_PASS` | 1 passed, 1 failed |
| `PASS_TO_PASS` | All listed tests passed |
| Agent finish status | `limit` at 32 tool calls |
| Model-reported token use | 305,300 prompt; 10,374 completion |

The patch rejected a dot in a blueprint name, satisfying
`test_dotted_name_not_allowed`. It did not update the related endpoint-name
error contract: `test_route_decorator_custom_endpoint_with_dots` expected
`ValueError`, but the existing endpoint check raised `AssertionError`.
The official report marked the instance unresolved without an infrastructure
failure. The grader's full test process also printed many failures from its
historical dependency environment; the official instance report above is the
source for the `FAIL_TO_PASS` and `PASS_TO_PASS` counts.

This run also exposed a runtime limit: commands execute on the local host, so
the agent spent part of its tool budget trying to install old Flask dependencies
in that environment. Evaluation workspaces should provide the task's dependency
environment and isolate commands. Cobalt 0.1.0 records and gates those commands
but does not sandbox them.
