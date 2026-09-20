# Project implementation rules

Build a complete local Chinese research-agent workbench on Agno AgentOS.

- Never commit or upload the original DOCX/PPTX files or their full extracted text. Runtime data, secrets, reports, and uploads stay in ignored `.local/`.
- All paid model requests MUST pass through `backend/app/model_gateway.py`. No direct DeepSeek/OpenAI HTTP or SDK calls elsewhere. The cumulative project budget is CNY 300; the gateway reserves worst-case cost before requests and retains uncertain charges.
- Read `docs/ARCHITECTURE.md` for the shared API contract. Keep frontend and backend compatible.
- No fake success, fabricated citations, precomputed metrics, or unlabeled model simulation. Offline rehearsal must be visibly labeled.
- Bind local servers to 127.0.0.1 by default. Enforce roles in backend endpoints. Never expose unbudgeted AgentOS model routes.
- Use small synthetic/public-safe sample data only; no original project-background material in model calls.
- Every meaningful change must have appropriate verification. Keep a development log in docs/IMPLEMENTATION_PLAN.md.
