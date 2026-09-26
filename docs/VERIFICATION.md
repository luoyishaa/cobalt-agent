# Workspace-version evidence

## Contract

Successful command evidence belongs to a particular observed workspace version.
It is not a permanent property of the task or a guarantee that a command is a
meaningful test.

1. The tool gate observes the workspace before and after each approved command
   or file write. Results report created, modified and deleted paths, including
   changes left behind by a command that exits unsuccessfully.
2. A command can contribute evidence only when it exits successfully, both
   observations are complete, and its before/after fingerprints match. A command
   which changes observed files cannot validate its own result.
3. Later observed changes or a failed command clear previous successful-command
   records. Observations after model responses detect external edits made while
   the model was responding. Existing dirty files form the initial baseline and
   are not attributed to this run.
4. Surviving changed files must be reread. The returned digest must match the
   observed current version; a read overtaken by another writer cannot satisfy
   this requirement. Deleted files are recorded without requiring a reread.
5. Incomplete observation prevents `completed` for the remainder of this run.
   The result reports `observation_errors`. Read failures are not interpreted as
   proof that a file was deleted.

`verified_commands` is retained for compatibility. Its entries are successful
commands without observed changes, supporting the result's
`verification_fingerprint`. The name does not mean the runtime understands the
commands' test coverage. A successful `echo` remains a successful command, not
proof of application correctness. Benchmarks use separate external verifiers.

## Observation scope

Snapshots hash regular-file contents and permission bits, indexed by relative
path. Symbolic links are represented by their targets without traversing them.
Hashing uses streaming [file digests](https://docs.python.org/3/library/hashlib.html#hashlib.file_digest);
directory traversal uses [os.walk](https://docs.python.org/3/library/os.html#os.walk)
with an explicit error handler.

Runtime state, Git metadata, dependencies and interpreter/test caches are
excluded: `.cobalt`, `.git`, `.venv`, `node_modules`, `__pycache__`,
`.pytest_cache`, `.ruff_cache`, `.mypy_cache`. Private `.env` files are excluded;
`.env.example` is included. Empty-directory changes, timestamps alone, ACLs,
external resources, network effects and database state are not covered.

The scan is not a filesystem transaction. Concurrent changes can occur between
observations, a command can change and restore a file between snapshots, and
another writer can act after the last observation. These cases are not proven
safe by matching fingerprints. The scan cost is proportional to the bytes in
scope; large-repository performance has not been established. The evidence
window is the current `ask` invocation; earlier runs are not re-certified on
resume.

## Executable boundaries

`tests/test_version_evidence.py` checks the public agent/tool interfaces:

| Boundary | Required observation |
| --- | --- |
| Check succeeds, then a command edits a file, then reread | `unverified`; old command evidence removed |
| External write while the model answers | `unverified`; changed path recorded |
| Later command fails after an earlier success | Previous evidence revoked |
| One failed command creates, modifies and deletes files | All three changes reported; no successful evidence |
| Edit, subsequent successful check, fresh read | `completed`; fingerprint matches the observed current files |
| Delete, subsequent check confirming absence | Completion allowed without rereading a missing file |
| Snapshot reports an injected I/O failure | `unverified`; observation errors retained |
| Pre-existing work is only read | No changes attributed to this invocation |
| File changes after reading but before delivery | Old read cannot clear the refresh requirement |

These are deterministic boundary checks, not a measured probability that a
language model will choose the correct actions. Process-interruption tests and
live coding tasks exercise separate contracts described in [EVALUATION.md](EVALUATION.md).

## Finalization feedback and delivery

When a model proposes a final answer, the runtime collects missing post-edit
reads (including newly created tests), missing successful-command evidence for
the current workspace version, and unsupported source citations. It provides
the concrete checklist and a bounded excerpt of the rejected draft in the next
model request. Drafts and rejection reasons are recorded as journal events.

At most two finalization rejections are permitted per `ask`; partial progress
does not reset the allowance. Tools used to satisfy the checklist consume the
original tool budget. A normal completion needs no extra model request.
Incomplete filesystem observation is not treated as something another model
answer can repair, and receives no finalization retry.

If evidence remains incomplete, `answer` contains a runtime-generated
`Unverified` notice and the missing evidence. The raw final candidate is retained
in `RunResult.model_answer` and `result.json`, not delivered as the accepted
answer or inserted into subsequent conversation history. Rejected candidates
remain in the journal. Completed answers retain normal model prose. Tool-limit
results preserve pending read paths and the current verification fingerprint.

This prevents an unverified draft's confident wording from overriding the
runtime verdict. It does not judge all natural-language claims or determine
whether a successful command was a relevant or comprehensive test. Withholding
the whole unverified draft can hide useful explanation; it remains available in
the run record. There is no semantic claim classifier or second reviewing model.

### Measured continuation

Clean commit `f7c6245` reran the existing revised-requirement task, three attempts
for each context policy, with the same 24-tool and 48,000-character limits:

```powershell
python -m scripts.evaluate_multifile --case revised_requirement --repeat 3
python -m scripts.audit_multifile_zero benchmarks/results/multifile-20260926-141036.json
```

| Six attempts | Earlier run | Finalization rerun |
| --- | ---: | ---: |
| Original functional verifier | 6/6 | 6/6 |
| Functional verifier plus zero-price audit | 5/6 | 6/6 |
| Runtime evidence complete | 3/6 | 5/6 |
| Attempts receiving feedback | 4 | 3 |
| Feedback requests | 4 | 4 |
| Tool calls | 101 | 93 |
| Input tokens | 387,410 | 356,889 |
| Output tokens | 40,905 | 34,377 |

In the rerun, two of three feedback recipients completed their evidence. The
remaining model repeated "Repair complete and verified" after two reminders,
without reading `validate.py`. Its delivered answer was instead the unverified
notice naming that file; the draft was preserved separately. There was no
infinite retry and no tool-budget increase.

Before data: revised-requirement rows in
`benchmarks/results/multifile-20260925-142920.json` and its zero audit. After data:
`benchmarks/results/multifile-20260926-141036.json` and its zero audit. This is a
small before/after diagnostic with stochastic model behavior, not a controlled
estimate of quality improvement, latency or cost savings.

Five dedicated integration tests cover normal completion, successful checklist
repair, noncompliance with preserved drafts, partial progress without allowance
reset, and exhausted tool budget. Complete regression: 88 tests, 87 passed and
one Windows symlink-permission case skipped; Ruff passed. A UTF-8 read in the
new result-record test was corrected after the experimental commit; runtime and
live grading behavior were unchanged.
