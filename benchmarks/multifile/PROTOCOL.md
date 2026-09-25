# Multi-file continuation protocol

Frozen before live attempts. Entry points are `Agent.ask` and a fresh `Agent`
restored with a session ID. Compare budget-only omission and historical omission
with the same model, 48,000-character budget and 24-tool limit. Three attempts
per case and policy, alternating policy order, two workers, no seed override.

The fixture is an order quote library with configuration, pricing and API files.
Public requirements: integer cents, quantities, half-up tax on the entire
subtotal, shipping after tax, free shipping at an inclusive subtotal threshold,
zero charge for an empty order, unchanged input and a stable response shape.
Configuration must remain effective at runtime. Unrelated branding must survive.

1. **Cross-file repair:** retain earlier requirements across three noisy logs.
2. **Revised requirement:** change only tax from 17% to 9%; retain all other rules.
3. **Interrupted continuation:** a real bootstrap command increments a receipt
   and fixes tax configuration, then a gate raises before its tool reply is
   saved. A human branding edit follows. Restore a fresh agent and finish the
   repair without repeating the bootstrap or overwriting branding. This injects
   the effect/result-persistence gap, not an OS process-kill benchmark.

Setup model responses are scripted; all continuation decisions use live DeepSeek.
Logs contain differing legacy advice, marked as observations rather than user
requirements. Their original bodies and archives are available to both policies.

The verifier executes the public `api.quote` entry point in a separate interpreter
after the agent finishes. Held-out inputs and grading code are not put inside the
task directory. This is test separation, not an adversarial security sandbox.
It checks literal boundary examples, runtime configuration changes, input
immutability, preserved branding and the bootstrap receipt's execution count.
The receipt is workspace data, not a tamper-proof external effects ledger;
command traces must also be inspected. Visible checks and bootstrap source must
remain byte-identical.

Report task correctness separately from runtime status, token use, tool calls,
retrievals, rejected edits and elapsed time. A completed runtime is not a pass.
Fixtures must fail before repair; a known correct implementation must pass.
Mutated implementations must fail the corresponding verifier checks. Keep all
attempts, including infrastructure failures; do not change the oracle after
seeing model output. Any later protocol revision requires a separate run.
