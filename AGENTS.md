# Codex project instructions — BIRD Text-to-SQL

This is an **incremental** Python BIRD Mini-Dev Text-to-SQL project, not a greenfield rebuild. Read `CODEX_PLAN.md` and `README.md` before editing. Act on **only the phase explicitly requested** by the user. Do not start the next phase automatically.

## Repository layout

- `bird_starter/data.py`: cases and sqlite path discovery.
- `bird_starter/model.py`: model adapter; never log plaintext keys.
- `bird_starter/tools.py`: safe read-only SQL schema/execute functions.
- `bird_starter/workflow.py`: LangGraph nodes and routing.
- `bird_starter/evaluation.py`: local result-match **proxy**, not official BIRD EX.
- `bird_starter/mcp_server.py`: local stdio MCP server; currently Graph does **not** use MCP transport.
- `run_eval.py`: CLI experiment runner.
- `tests/`: offline pytest fixtures.

## Non-negotiable rules

1. Gold `SQL` in BIRD records is exclusively for offline evaluation. Never place it in LLM prompts, Graph State, backend schema hints, MCP tool responses or repair messages. Only the evaluator can access it.
2. Never create or fake benchmark data. Tiny synthetic SQLite fixtures may be used **only** in unit tests, and must be described as fixtures. Real BIRD files are downloaded separately by the user.
3. Maintain SQL-only, read-only execution and bounded resource use; guard against destructive or multiple statements. Do not weaken SQLite URI `mode=ro` and `PRAGMA query_only=ON`.
4. Keep evaluation comparable: stable `case_id`, same questions/model/evidence policy, fixed configuration. Never label the local proxy as official EX.
5. Keep tests offline by default. User-supplied API keys from environment only; no secrets or dataset binaries in commits. Never silently consume paid API credits in tests.
6. Prefer minimal changes and standard-library code where sufficient. Add file-level tests for all behavior changes. Be explicit about missing data/dependencies rather than claiming success.
7. If instructions conflict with secure operation or evaluation integrity, preserve security/evaluation integrity and explain the conflict.
8. Avoid unnecessary frontend, K8s, vector DBs, Docker complexity in MVP.

## Work cycle

- Read phase acceptance criteria in `CODEX_PLAN.md`.
- Inspect existing implementation first; outline files/risks; implement only that phase.
- Run `python -m compileall bird_starter run_eval.py` and `pytest -q` when dependencies are installed; run phase-specific checks if applicable.
- Summarize `Stage / Files / Tests / Proof / Risks / Next` with true test results. Stop after one phase.
