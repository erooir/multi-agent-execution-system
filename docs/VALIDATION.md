# Demo acceptance record

Verified locally on Windows on 2026-09-20. No private background document was uploaded, indexed automatically, or sent to an external model.

## Automated checks

- Backend: 65 tests passed. Includes budget reservation concurrency, uncertain charges, endpoint/model allowlist, local-only evidence blocking, role enforcement, workflow validation/versioning, real Agno execution over mocked model HTTP, cancellation, report rejection/rework, output truncation detection, configured planner/writer instructions, report exports and 19 registration/authentication cases.
- Python: Ruff lint and formatting checks passed (20 files including the repository guard).
- Frontend: TypeScript and Vite production build passed; twelve tests cover DAG layout, legacy-layout collision handling and citation rendering while preserving Markdown links/code. Accessible form labels were checked against rendered markup.
- One dependency deprecation warning remains in Starlette's test client for an AnyIO alias; it does not fail tests.

## Actual application checks

- Authenticated browser session through the Chinese product UI.
- Live DeepSeek generated a 12-node workflow containing evidence checks, alternate paths, batch analysis, report generation and final review; draft saved and published through the canvas.
- Live execution retrieved 18 local evidence chunks, ran analysis, and produced a report draft. A truncated output found during acceptance was rejected through the review UI. The gateway now checks provider completion reasons and fails incomplete output rather than recording success. Agno's implicit node retries and error-skipping are explicitly disabled.
- Retrying with review feedback regenerated a complete six-section report; review edits and a functional-test approval were submitted through the UI. This approval validates the software workflow, not research conclusions.
- A fresh live run then completed every active node from start through batch analysis, report, review and end without retries or output truncation. It produced a complete 1,536-character Markdown draft with 18 available source chunks. Bound/default writer and planner instructions are applied; disabled agents block calls.
- Report-body citations render as compact numbered buttons. Clicking a citation opened the correct source excerpt, location and download link. Markdown, HTML and DOCX exports returned valid artifacts.
- All 30 synthetic samples ran in local rehearsal mode through the actual API and reached their required review gate with analysis and resolvable evidence: 30/30 rule checks passed. This does not mean human review was completed or that model factual accuracy is 100%.
- Keyword retrieval without selecting an individual document returned project-scoped evidence. Local OCR and cached BGE semantic retrieval were exercised; a real metered image-understanding call used a locally generated synthetic image.
- Desktop and narrow preview layouts inspected. Graph view, workflow canvas, review forms and execution timeline loaded correctly.
- Windows stop/start verified, including the virtual environment's child interpreter and release of port 8000.

## Cost and deployment scope

At acceptance, fourteen metered requests had a conservative total of CNY 0.361968 and no unresolved reservations. This ledger estimate is based on twice the verified peak token rates, not the provider invoice. The persistent project ceiling is CNY 300. Tests use mocks and make no paid calls.

The current implementation is a local demo with three local roles, synthetic sources and a private AgentOS runtime. Isolation-ready model substitution, production identity, tenant isolation, automatic knowledge-graph extraction and expert quality evaluation remain follow-up work.

## Interface and account refinement

- Applied the user-selected **航智 · 航空情报平台** brand and dark collaboration direction, with clearer workflow nodes. The four comparison mockups and official references remain available at `/style-options.html` and in `docs/DESIGN.md`.
- Verified ordinary registration through the actual browser: automatic researcher sign-in, logout, later login and disabled administrator settings. The temporary verification identity was removed after logout; audit history was retained. Existing administrator sessions continued working after the backend restart.
- Registration tests cover salted password hashes, case-insensitive duplicates, concurrent registration, extra role-field rejection, current persisted role/enabled checks, session rotation/logout, origin checks, persistent rate limits and one-time legacy migration.
- Inspected all ten work areas, dark login/registration, knowledge graph, workflow configuration, execution timeline and report source modal. Long reports retain a readable light paper surface.
- Checked desktop at 1440 px and narrow screens at 390 px. Navigation collapses into a drawer; task input and authentication forms remain usable without page-wide horizontal overflow. Body text is 16 px; supporting text is at least 13 px before canvas zoom.
- Legacy flow positions that overlap the larger node cards are adjusted in memory with a visible layout-preview indicator. Saving remains explicit; valid non-overlapping positions and execution data are preserved. The editor opens at a readable 1× zoom near the start node and retains full-graph overview and return-to-start controls. Module navigation returns to the top without disrupting periodic data refreshes.
- This refinement made no paid model calls; the persisted budget ledger remains unchanged.
