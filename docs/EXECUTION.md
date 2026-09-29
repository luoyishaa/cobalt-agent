# Execution and task checks

## Local and container modes

The CLI defaults to local execution. It asks for approval before each edit or
command. `--yes` is accepted only with `--execution container`.

Container mode makes a persistent task copy under the operating system's
temporary directory. It omits `.env` files, Git history, symlinks, dependency
directories, and Cobalt's own runtime directory. The copy receives a new
one-commit Git baseline. File edits and commands operate on that copy; the
original repository is untouched. Review `final.patch` in the printed run
directory before applying a change to the original repository. To resume a
container run, pass the printed isolated workspace to `--workspace` with
`--resume latest` and the same image.

The default image is `cobalt/python:3.11`. Build it once with:

```powershell
docker build -t cobalt/python:3.11 -f docker/Dockerfile .
python -m cobalt --doctor
```

The command container has no network, a read-only root filesystem, a temporary
`/tmp`, and CPU, memory, process and privilege limits. Only the isolated task
copy is mounted read-write. Model credentials stay in the host process and are
not passed through to the command container. An image must already contain the
task's dependencies. If Docker or the image is missing, Cobalt stops; it does
not switch to host execution. Docker is an isolation layer with its own daemon
and configuration; do not treat it as a proof against every host attack.
If an evaluation image contains a repository at `/testbed`, that image path is
covered by an empty temporary filesystem. The agent works from `/workspace`.
The isolated Git baseline preserves file bytes across Windows and Linux and
marks only `/workspace` as a safe repository for Git commands in the container.

Use `--image` for another prebuilt image. `--container-env NAME=VALUE` explicitly
sets a non-secret variable inside command containers. It does not inherit local
API keys. This is useful when a historical project's Python environment or
source directory needs to be selected. Run `python -m cobalt --doctor --image
IMAGE` to check availability.

## Explicit checks

Free-form tasks remain supported. Their saved report labels passing commands
chosen by the agent as `self_checked`. For repeatable tasks, supply a JSON file:

```json
{
  "request": "Fix empty grade averages while preserving non-empty behavior.",
  "checks": [["python", "-m", "unittest", "tests.test_grades", "-q"]],
  "protected_paths": ["tests/test_grades.py"]
}
```

```powershell
python -m cobalt --workspace path\to\repo --task-file task.json
python -m cobalt --workspace path\to\repo --report run-012345abcdef
```

Each run saves `result.json`, `report.json`, `final.patch`, and an event log under
`.cobalt/runs/<run-id>/`. The report distinguishes agent termination from task
validation. User-provided checks are run after the agent, and changes to
protected files fail validation. Without explicit checks, `completed` says only
that the agent finished its run. A passing check proves that command succeeded
on the observed workspace; it does not prove every requirement was covered.

## Public issue protocol

`scripts/select_swebench.py` pins a dataset revision and deterministically
selects one instance from each of ten repositories. `scripts/attempt_swebench.py`
checks out each base commit, requires its official task image locally, runs one
isolated Cobalt attempt, and saves predictions plus raw attempt records. The
script refuses a dirty source tree for the test split and records an attempt
before starting inference. It uses a fixed 48-tool-call ceiling per issue.
Prediction patches omit test-file changes while retaining the unfiltered patch
and excluded path list in the raw attempt record. `scripts/summarize_swebench.py` checks the fixed set,
raw attempts, reports, and official result against the release gate. The
official SWE-bench harness grades predictions separately. See
[the earlier public issue attempt](SWEBENCH.md) for the grader format and a
failure example. Generated test manifests and all attempted results stay visible;
one ten-task diagnostic set is too small for a general success-rate claim.
