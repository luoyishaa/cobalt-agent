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
