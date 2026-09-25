# Multi-file continuation evaluation

The [frozen protocol](../benchmarks/multifile/PROTOCOL.md) tests cross-file repair,
revised requirements and interrupted continuation with a concurrent human edit.
Setup is scripted with real commands. Continuations use live DeepSeek
`deepseek-flash`; each policy gets three attempts per case, a 48,000-character
context budget and 24 tool calls. Both policies use the same runtime and tools.

## Initial run

Clean commit `be05afa`, report
`benchmarks/results/multifile-20260925-142920.json`:

| Measure | Budget-only omission | Historical omission |
| --- | ---: | ---: |
| Cross-file repair | 3/3 | 3/3 |
| Revised requirement | 3/3 | 3/3 |
| Interrupted continuation | 3/3 | 2/3 |
| Original verifier total | 9/9 | 8/9 |
| Runtime completed | 6/9 | 4/9 |
| Input tokens | 718,949 | 458,932 |
| Output tokens | 56,926 | 56,131 |
| Tool calls | 153 | 154 |
| Summed task seconds | 263.045 | 261.704 |

The failed historical attempt executed the interrupted bootstrap again, raising
its receipt from 1 to 2. Its pricing code and preserved branding passed. This is
a recovery failure despite a `completed` runtime status. It does not establish
that output omission caused the decision; both policies shared the permissive
recovery behavior and the model is stochastic. Lower token use at different
success rates is not evidence of cheaper equivalent-quality work.

Eight attempts ended `unverified` because newly created validation files were
not reread, including after a runtime reminder. Some model prose still claimed
verification. The machine status and appended evidence notice exposed the gap;
prose consistency remains an open issue. Ten attempts triggered the reminder;
two subsequently satisfied it.

## Supplementary boundary audit

Source review found a hole in the frozen verifier: a nonempty order containing a
zero-price item. It remains below the free-shipping threshold and owes 300 cents
shipping. Two implementations incorrectly treated a zero subtotal as an empty
order. The original scores were preserved; a separate offline audit executes
the saved candidate sources, without another model call:

```powershell
python -m scripts.audit_multifile_zero benchmarks/results/multifile-20260925-142920.json
```

The additional check passed 16/18. Combined with the original checks, the actual
observed result is **15/18**: budget-only **8/9**, historical **7/9**. The two
additional failures were budget-only/revised_requirement/1 and
historical/interrupted/2. Raw audit:
`benchmarks/results/multifile-20260925-142920-zero-audit.json`.
Neither result proves all requirements or inputs are covered.

## Recovery fix and targeted rerun

The runtime now retains an [exact interrupted-command replay guard](RECOVERY.md)
after inspection. A deterministic public-interface test failed with count 2
before the fix and passed with count 1 afterward, including a second restart.

Clean commit `9790b8d` reran only the frozen interrupted scenario:

```powershell
python -m scripts.evaluate_multifile --case interrupted --repeat 3
```

Report `benchmarks/results/multifile-20260925-143534.json` contains six attempts.
Both policies passed 3/3; all six also passed the supplemental zero-price audit
(`multifile-20260925-143534-zero-audit.json`). All receipts stayed 1 and all human
branding edits survived. Runtime status was completed for 5/6; one validation
file reread remained missing. No live attempt requested the blocked exact replay:
the live evidence supports successful continuation, while deterministic tests
exercise the denial path. These six attempts are not a rerun of all 18 cases.

| Recovery rerun measure | Budget-only | Historical |
| --- | ---: | ---: |
| Input tokens | 295,028 | 240,914 |
| Output tokens | 21,072 | 19,484 |
| Tool calls | 55 | 54 |
| Summed task seconds | 102.642 | 93.797 |

The small samples are diagnostic, not estimates of general model quality,
latency or billed-cost savings. Receipt files and source snapshots are not an
adversarial effects ledger. The interruption is an injected effect/result-save
gap; OS-kill behavior is covered separately by local tests. Exact command
blocking does not detect semantically equivalent commands.

Local regression after the fix and audit ran 83 tests: 82 passed and one
Windows symlink-permission case was skipped. Ruff passed. Next work should
address final-answer evidence consistency and independently specified task
boundaries; no benchmark-specific pricing rules were added to the runtime.
