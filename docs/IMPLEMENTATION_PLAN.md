# Implementation plan

## Delivery gates
1. Foundation: private-file exclusions, GitHub write access, environment, SQLite store, role sessions, model-budget gateway.
2. Working vertical slice: Agno-backed planning and report generation, actual document retrieval, editable valid workflow, human review and persistent execution.
3. Product modules: projects, agents, six skills, six workflow templates, run center, evidence-backed report versions, 30 samples, evaluation and audit/settings.
4. Verification: budget concurrency/uncertain responses, permissions, source isolation, workflow validation/recovery, API integration, frontend build and browser end-to-end.
5. Handoff: reproducible startup, public-safe Git commits, GitHub push, local preview and user guide. No original background documents or local data in Git.

## Implementation status
- Foundation and product modules implemented. Original documents, uploads, databases, model caches and credentials are excluded from Git and protected by a staged-file guard.
- Chinese UI includes ten work areas: overview, projects, knowledge, agents, skills, workflow editor, runs, reports/reviews, evaluations and system/audit.
- Six published starter workflows, three synthetic research scenarios, four agent roles and four report structures are available. All six skills have real implementations; no placeholder success responses.
- Real DeepSeek planning produced a 12-node workflow with conditional and batch paths. Real analysis and draft generation were exercised through the browser. Review rejection and persisted feedback were verified.
- All 30 seeded samples were executed through the local API in rehearsal mode and passed the execution/analysis/citation rules, stopping at the required human-review gate. This is not a model quality or factual accuracy score.
- Verified local OCR, cached BGE semantic retrieval and paid image understanding against a synthetic image. No original background material was sent to a model.
- Documentation and Windows startup/stop scripts are available. The startup script builds and serves the frontend on loopback; the stop script handles the Windows virtual-environment interpreter child.
- Final acceptance records, exact test counts and model spending are maintained in the handoff report.

## Follow-up deployment scope
After client feedback: production identity and tenant isolation, deployment packaging, controlled local-model inference, automatic graph extraction and stronger expert evaluation. These are not prerequisites for the current local demo and must not be presented as already delivered.

## Identity and interface refinement (2026-09-20)
- User selected the name **航智 · 航空情报平台** and direction D, a dark collaboration workspace inspired by Linear, with direction C's clear workflow nodes inspired by Dify. The four original comparison mockups remain at `/style-options.html`; references and design boundaries are documented in `docs/DESIGN.md`.
- Increase body, table and form text to 15–16 px, supporting text to at least 13 px. Apply the selected theme across authentication, navigation, resources, canvas, execution, review and settings while preserving readable report pages.
- Add ordinary self-registration and persistent password-hashed accounts with researcher permissions, retaining existing identities. Remove demonstration credential and backend-permission wording from the sign-in screen.
- Verify registration/login, duplicate and privilege checks, account persistence, desktop/narrow layouts and workflow controls without additional paid model calls. Keep private source documents and runtime data outside Git.
