# Evaluation

We report three different claims separately:

1. **Runtime contract:** local tests check path isolation, stale-write
   rejection, read-only mode, tool feedback, session resume, and evidence
   labeling. A scripted model makes these checks deterministic.
2. **Task outcome:** a live model attempts each task in a fresh fixture copy.
   For repair cases, an external command checks the final repository. For
   question cases, the evaluator requires a real read and expected answer
   terms.
3. **Process evidence:** each row records the agent's status, tool calls,
   successful commands, elapsed time, and provider-reported token use.

Run the local checks:

```powershell
python -m unittest discover -s tests -v
ruff check src tests scripts benchmarks/fixtures
```

Run the live set after configuring `DEEPSEEK_API_KEY`:

```powershell
python scripts/evaluate_live.py
```

Results appear as dated JSON files in `benchmarks/results/`. The fixture code,
task prompts, and external verifiers are in `benchmarks/` so each result can be
inspected and repeated. These small tasks are a smoke benchmark, not a claim
about general coding success. The repository will add harder tasks and failure
categories before using a success rate in a resume.
