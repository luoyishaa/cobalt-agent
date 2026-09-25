# Interrupted command recovery

A tool request is saved before execution. If execution stops before its result
is saved, the restored session marks the result `interrupted`, meaning unknown.
The effect may have happened even if no success response exists.

Recovery has two independent restrictions:

1. Before another effectful action, the model must receive a successful current
   workspace inspection. An archive read alone is not current inspection.
2. An interrupted `run_command` cannot automatically run again with identical
   `argv` in that session, even after inspection. Changing the call ID or timeout
   does not remove this restriction. The restriction is reconstructed from the
   durable transcript on restart, independently of the shortened model view.

Independent reads, repairs and check commands may continue after inspection.
The tool returns a denial for exact replay and the journal records
`interrupted_command_replay`. If retry is actually necessary, stop to reconcile
the original effect manually. There is currently no in-session override API.

## Why inspection is insufficient

A live continuation read a migration receipt with count 1, reran the migration,
and raised the count to 2. It interpreted the old receipt incorrectly while
preserving the human's file edit and correctly repairing application logic.
An inspection proves that information was obtained; it does not prove the next
side effect is safe. See [the multi-file results](MULTIFILE.md).

## Limits

This is an exact-command guard, not command equivalence analysis or exactly-once
execution. A different executable spelling, wrapper or alternate command body
can repeat the same effect without matching. A new session has no history of
the interruption. The current guard also blocks exact retries when the process
actually stopped before producing an effect, and blocks identical check commands
whose harmlessness the runtime cannot establish. This is a deliberate recovery
availability cost. Do not bypass it by reformulating a command.

Other tools retain their existing protections (create without overwrite and
digest-checked edits); this guard does not identify all external side effects.
External APIs need their own operation identities and reconciliation contracts.

## Tests

`test_inspection_does_not_authorize_replaying_unknown_command_even_after_restart`
executes a counter command, interrupts before saving its reply, inspects the
counter, attempts exact replay with a new call ID and timeout, and restarts again.
The count remains 1 and an independent check still succeeds. Before the fix the
same test failed with count 2. Existing killed-worker tests cover separate
before-effect, after-effect and after-result process boundaries.
