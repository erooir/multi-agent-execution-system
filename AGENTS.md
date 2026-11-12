# Project implementation rules

Build the production Hangzhi agent management platform on self-hosted Agno AgentOS. Read docs/ARCHITECTURE.md for the v2 shared contract. Legacy business flows are archived in Git.

- Never commit or upload original Office files, extracted full text, handoff packages, secrets or runtime data. Keep them in ignored .local/.
- All paid model calls MUST pass through backend/app/model_gateway.py. Preserve historical .local/budget.sqlite3, CNY 300 cumulative cap, worst-case reservations and uncertain charges. Never reset the ledger when changing business storage.
- Separate asset definitions, immutable versions and task instances. Enforce project permissions, tool policy, snapshots and reference protection in the backend.
- No human review wait states. Deterministic checks and automatic review lead to bounded revision or honest terminal outcomes.
- No fake success, fabricated citations, precomputed metrics or unlabeled simulation. Rehearsal is not offline inference.
- Bind to 127.0.0.1 by default. Never expose unbudgeted AgentOS model routes. Disable telemetry and runtime downloads for offline operation.
- Verify with small synthetic/public-safe data; never send private background materials to models.
- Every meaningful change requires appropriate verification. Keep docs/IMPLEMENTATION_PLAN.md updated.
- This development starts at main f36f490643da4c7e7fc0ba90daf602c44caa2828 on codex/autonomous-task-platform. Commit and push this branch only. Do not merge, cherry-pick or import the concurrency branch; do not merge any branch without the user's explicit instruction. No force pushes or rewritten history.
- Implement sequential task execution; concurrency is outside this iteration. Ordinary users supply goals and data, while the platform plans, selects or creates task-scoped agents, executes, reviews and revises automatically.
- Generated data-processing scripts may run only through the controlled isolated script tool. Document Skills do not grant arbitrary host execution. New task agents require independent evaluation before reuse.

