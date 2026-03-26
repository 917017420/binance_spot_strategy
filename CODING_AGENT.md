# Coding Agent Template

You are a coding agent working in `/root/.openclaw/workspace/binance_spot_strategy`.

## Mission
Implement the requested change with small, production-minded edits.

## Working rules
1. Read the relevant files first; do not guess.
2. Prefer reusing existing structures over inventing parallel ones.
3. Keep changes incremental and coherent.
4. Do not auto-commit unless explicitly asked.
5. Run focused tests first, then broader tests if needed.
6. At the end, always report:
   - files changed
   - behavior change summary
   - tests run
   - remaining risks / next best step

## Current project expectations
- This repo is building a long-running automated execution control plane.
- Priorities are:
  - stable scan -> decision -> queue -> submit -> reconcile flow
  - correct active-position management
  - correct reduce/exit/release semantics
  - good control-plane observability
- Do not let simulation artifacts pollute live control-plane truth.
- Treat real live positions/inflight state as the authoritative blockers.
- After true exit completion, operational residue should be cleaned automatically.

## Task block
Fill in these fields before launch:

TASK:
<what to build/fix>

SCOPE:
<files/modules allowed>

ACCEPTANCE:
<how success is judged>

TESTS:
<what to run>
