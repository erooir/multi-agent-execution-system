# Implementation plan

## Delivery gates
1. Foundation: private-file exclusions, GitHub write access, environment, SQLite store, role sessions, model-budget gateway.
2. Working vertical slice: Agno-backed planning and report generation, actual document retrieval, editable valid workflow, human review and persistent execution.
3. Product modules: projects, agents, six skills, six workflow templates, run center, evidence-backed report versions, 30 samples, evaluation and audit/settings.
4. Verification: budget concurrency/uncertain responses, permissions, source isolation, workflow validation/recovery, API integration, frontend build and browser end-to-end.
5. Handoff: reproducible startup, public-safe Git commits, GitHub push, local preview and user guide. No original background documents or local data in Git.

## Current status
- Architecture and API contracts established.
- Original DOCX/PPTX references excluded from Git.
- DeepSeek credential exists in Windows user environment (not printed or written).
- Official current Flash peak rates verified: CNY 2 / 1M uncached input, 8 / 1M output. Conservative accounting will ignore discounts and reserve before calls; project cap CNY 300.
- Implementation in progress.
