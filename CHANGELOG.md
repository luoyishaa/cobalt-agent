# Changelog

## 0.1.0 — 2026-09-29

First runnable release of the local coding-agent harness.

- Conversational CLI with read-only and editing modes, approval gates, and
  replace operations guarded by the digest of a prior read.
- Persistent run events, resumable sessions, explicit handling of interrupted
  tool effects, and retrieval of full command output after context compaction.
- Post-edit read and verification checks that can mark a final answer
  unverified when the available evidence is insufficient.
- Configurable model provider presets, local environment configuration, and
  fresh-workspace live evaluation with independent repair verifiers.
- A Windows demo that exercises a real model and an external verifier.

The tool handles bounded local repository tasks. It does not provide a web UI,
remote execution service, or a guarantee that a completed run is correct.
