# Demo acceptance record

Verified locally on Windows on 2026-09-20. No private background document was uploaded, indexed automatically, or sent to an external model.

## Automated checks

- Backend: 46 tests passed. Includes budget reservation concurrency, uncertain charges, endpoint/model allowlist, local-only evidence blocking, role enforcement, workflow validation/versioning, real Agno execution over mocked model HTTP, cancellation, report rejection/rework, output truncation detection, configured planner/writer instructions and report exports.
- Python: Ruff lint and formatting checks passed (19 files).
- Frontend: TypeScript and Vite production build passed; eight tests cover DAG layout and citation rendering while preserving Markdown links/code. Accessible form labels were checked against rendered markup.
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
