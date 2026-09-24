# Text replacement

`replace_text` checks the SHA-256 of the original bytes before matching a unique
text block. Uniform LF and CRLF files accept either newline spelling in the
request. Replacement text uses the existing file's newline convention; bytes
outside the replacement are preserved. This avoids failed multiline edits when
line-numbered reads hide the underlying newline representation.

Whitespace and indentation remain exact. Repeated matches are rejected after
newline conversion. Files with mixed newlines or bare CR reject multiline
requests; single-line changes without new newline characters remain possible.
No whole-file normalization or fuzzy matching is performed.

Live multi-turn repairs exposed this boundary: models repeatedly attempted an
LF block in a CRLF fixture, then inspected raw bytes or switched editing methods.
Some correct repairs reached the tool-call limit before finishing the workflow.
Deterministic regressions cover both newline conventions, inserted lines,
ambiguous matches, whitespace mismatches and mixed-newline rejection. Existing
stale-version and permission-preservation checks remain in the suite.

## Multi-turn evaluation protocol

`scripts/evaluate_multiturn.py` compares context order using two independent
fresh workspaces per case. A scripted setup runs five real diagnostic commands.
Only the subsequent repair uses the live model. Both arms have the same model,
tools, character budget, call limit and task specification. Three attempts per
arm are intentionally small diagnostic samples.

The first case depends on an earlier 17 percent tax requirement. The second
explicitly replaces it with 9 percent in the latest message. An external
verifier tests computed values, integer return type and input preservation,
including empty input, half-up rounding and a large amount. The verifier must
fail before the model runs. It is not an adversarial OS sandbox.

Task correctness and runtime completion are reported separately. In the first
report, `false_completed` is a misleading legacy field name: it means only
runtime completion without a passing task verifier. The losing baseline
responses requested missing requirements, rather than falsely claiming a repair.
Later reports use `runtime_completed_without_task_success` for this condition.
Raw historical reports are retained unchanged.
