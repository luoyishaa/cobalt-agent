# Architecture

Cobalt runs one user-facing coding assistant. It can answer questions about a
repository, investigate failures, edit files, and run checks. A task type does
not create a separate agent. The same run loop applies a different set of tools
and evidence requirements to each request.

## Design rules

1. **Observed actions outrank fluent answers.** A repository answer without a
   successful repository tool is marked `unverified`. An edit without a later
   successful command is also marked `unverified`.
2. **One workspace root.** File tools resolve each path before access. Escapes,
   runtime directories, and local `.env` files are blocked.
3. **Edits are conditional.** `replace_text` needs the digest returned by
   `read_file`. If a human or another process has changed the file, the edit
   fails and the agent must read again.
4. **The model requests actions; the program decides whether to execute.**
   Native function calls supply a name and JSON arguments. The tool gate
   validates them, applies read-only mode, and asks for approval before writes
   or commands.
5. **Evidence survives a run.** An append-only event file records model turns
   and tool outcomes. A result file holds the outcome and measured usage.
6. **Failure stays visible.** Invalid model structure gets at most two recovery
   attempts; tool errors are sent back to the model. A timed-out command is
   terminated and cannot count as a successful check.
7. **Explicit source locations are checked.** A `file:line` reference in the
   final answer must point to lines read in the current run from an unchanged
   file. The agent gets one chance to correct unsupported locations. Otherwise
   the answer is marked `unverified` with the offending references recorded.
8. **A final answer follows a fresh read.** After a file tool changes a file,
   the runtime tracks that path until `read_file` returns its current content
   to the model. A final answer before that read gets one correction chance;
   if the read is still missing, the answer remains `unverified`.

## Main path

```text
CLI
  -> workspace + session + model adapter
  -> Agent.ask(request)
      -> select bounded conversation context
      -> ask model for structured tool calls or answer
      -> validate and execute tools through ToolGate
      -> record outcomes in session and run journal
      -> repeat, or return an answer with verification status
```

The `Agent` module has one public operation, `ask`. It hides turn bookkeeping,
limits, tool result feedback, and completion rules. `Workspace` owns the file
and command rules. `Model` is the external seam: Chat-compatible providers
share one adapter, while Claude uses a Messages adapter. Scripted adapters are
used in runtime tests.

The session file supports continuing a conversation after a new CLI process
starts. The context selector drops only complete older user turns, so no tool
reply is separated from its call. Each run records the selected context size,
dropped turn count, and whether the final request exceeded the character budget.
The evidence book retains file digests and requested line ranges as a small
navigation index. It does not manufacture a summary by taking the beginning of
a file. Changed files are labeled stale and must be read again; a fresh index
still requires a new read when details are needed. Older session files with
excerpt-only observations remain readable as location-only entries.

When the current user turn alone exceeds the character budget, the model view
may replace older results from repeatable read tools with explicit omission
markers. The captured tool replies stay in the session, and tool call/result pairs
remain intact. Final source references count only reads whose contents were
visible in the last model request. Older archived command outputs can also
be replaced in the model view with an exit code, result length and digest,
small verbatim output prefix and suffix, and an explicit omission warning.
These excerpts are labeled as raw text, not a semantic summary. Old archived
command logs yield space before small file reads; the latest command output
stays visible. Failure status and exit codes remain in omission markers;
unarchived failures and write results stay visible. If the request remains too large, the run stops
with `context_limit` before calling the model. This is a bounded character policy,
not a provider token count or a semantic compaction system.

Command output is streamed to temporary files. After completion or timeout,
`OutputStore` publishes a separate archive under `.cobalt/outputs/`: stdout,
an explicit stderr separator, then stderr. This layout does not preserve the
interleaved timing of both streams. The command reply contains a bounded preview
and an `output_id`; the session does not contain the complete large log.
`read_output` reads that archive by byte offset or literal UTF-8 search, returning
at most 8,000 bytes plus metadata. Byte offsets handle logs with extremely long
lines; UTF-8 sequences split at a page boundary are displayed with replacement.
The tool checks the archived digest before serving data. The file and metadata
are flushed before their directory is published. These choices follow Python's
[subprocess file redirection](https://docs.python.org/3/library/subprocess.html)
and [flush, fsync and replace APIs](https://docs.python.org/3/library/os.html).

Archived reads are historical observations. They do not create fresh file
citations, satisfy post-edit rereads, or clear the recovery inspection barrier.
They can themselves be omitted from a later model request, retaining a locator.
Search and integrity verification use bounded memory but scan the archive;
there is no search index, disk quota, automatic retention cleanup, or protection
against a malicious local command modifying both data and metadata. Archives
are workspace-scoped, not isolated between sessions or users.
Retrieval requires a known output ID; there is no archive discovery index for
IDs lost when older conversation turns leave the model context.
If archive publication fails after a command completes, the reply preserves
the actual exit code and reports the storage failure separately. It provides
no output ID and warns that only the bounded preview is available.

The runtime saves the user request and the model's tool-call request before a
tool runs, then saves after each tool reply. If a process stops between a tool
request and its reply, resuming the session inserts an `interrupted` reply that
says the effect is unknown. It never executes that call again automatically.
The next request must inspect the workspace before another effectful action.
The runtime denies writes and commands until a successful read, list, or search
result has been returned to the model. Inspection and a write requested in the
same model turn do not clear this barrier. The barrier is reconstructed from
the persisted transcript and its recorded resolution after another restart.
This protects against a false "the tool failed" conclusion; it does not make
an arbitrary external command transactional or guarantee exactly-once effects.

## Extension points

- **Models:** add another adapter that returns `ModelTurn`; keep provider wire
  formats out of the run loop.
- **Tools:** define a JSON schema and an implementation behind `ToolGate`.
  Changes to permissions and approval remain in one place.
- **Interfaces:** a future terminal UI or web client can call `Agent.ask` and
  read the run journal. Neither needs to duplicate tool policy.
- **Evaluation:** each case runs in a fresh copy of its fixture and gets an
  external verifier. Scripted runtime tests and live model tasks are reported
  separately.

## Trust limits

File path checks and approval reduce accidental damage. They do not turn an
approved arbitrary command into a sandbox: the command can access resources
permitted to the local user. Run unknown commands in a disposable environment.
The default CLI asks before commands and edits; `--yes` is intended for
controlled evaluation workspaces.

The evidence status is deliberately narrow. A passing command proves that
command exited successfully; it does not prove every requirement is met.
External verification in a benchmark provides a second, independent check.
The source-location audit checks provenance and freshness only. It cannot prove
that a cited line supports the surrounding prose, and it does not inspect
uncited claims. `completed` must not be interpreted as a factuality guarantee.
The post-edit read rule applies to changes made through file tools. An approved
arbitrary command may also change files without the runtime recognizing every
effect, so command approval is still a separate trust decision.
Conditional edits detect a changed file when the digest is checked; they do
not provide a transaction against a writer that races with the final replace.
New-file creation uses an atomic create-only link, while replacement retains
the existing file's permission bits. Session resume closes an interrupted tool
protocol without replaying the tool; it does not finish a partially executed
run or recover output that was never published. A published archive may survive
without its tool reply; the runtime does not infer success from an orphan archive.
A process that stops before a model
turn is recorded may leave an unanswered user request in the transcript.
Context size is logged. If safe omissions cannot fit the current turn, the run
stops before sending an oversized request.
