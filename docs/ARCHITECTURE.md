# Research Agent Workbench Architecture

## Runtime
Python 3.12+, FastAPI, Agno AgentOS, SQLite. React/TypeScript/Vite/React Flow frontend. Application data is under ignored `.local/`; model API keys are read from the process or Windows user environment and never stored there. Login identities and salted password hashes persist in the local database. No cloud control plane subscription. All model traffic uses the budget gateway.

AgentOS registers the same Agno Workflow that executes each validated node. Its unrestricted native API is private; the authenticated product API supplies role checks, persistence and spending control. At most two runs execute concurrently, with explicit recovery after interruption. Local deployment uses a single server process.

## Storage contract
`from .storage import store` singleton. `store.list(kind) -> list[dict]`, `store.get(kind,id) -> dict|None`, `store.save(kind,item) -> dict` (generates id/timestamps when omitted), `store.delete(kind,id) -> bool`, `store.audit(action,entity,detail,user='system')`. Generic JSON records, all changes atomic. Kinds: projects, agents, workflows, runs, reports, documents, chunks, evaluations, samples, events, approvals, graph_nodes, graph_edges, audits.

## API contract
All routes under `/api`. JSON collections are arrays (no envelope). Errors return HTTP errors with `detail`. Model is `deepseek-flash`, with a visibly labeled deterministic `rehearsal` execution mode, never silent fallback.

- POST /auth/register {username,password,name?} returns 201 {user} and signs in. Username is case-insensitive, 3–32 ASCII letters/digits/underscores; password 8–128 characters; display name at most 40 printable characters. Registration fixes the role to operator and rejects extra fields, including role. Concurrent duplicates are rejected by the SQLite record primary key.
- POST /auth/login {username,password}; GET /auth/me; POST /auth/logout. Sessions use opaque HttpOnly, SameSite=Strict cookies (Secure on HTTPS); only a token hash is persisted. User responses include id/username/name/role, never password hashes. Every authenticated request reads current role and enabled status from the user record. Passwords use PBKDF2-SHA256 with 600,000 iterations and random 32-byte salts; hashing runs off the event loop. Existing local identities and sessions migrate once without resetting passwords on later environment changes.
- GET /users; POST /users {username,password,name?,role}; PUT /users/{id} {name?,role?,enabled?,password?}. Admin-only user management: created roles are limited to operator/reviewer; disabling or password reset revokes sessions; the last enabled admin and self-lockout are protected.
- Authentication writes enforce allowed local origins and reject cross-site browser requests. Durable rate limits apply to registration (10 per IP per 10 minutes) and login (30 per IP and 10 per username per 10 minutes), returning Retry-After. Auth storage kinds: users, sessions, auth_meta, auth_limits.
- GET /bootstrap returns {projects,agents,skills,workflows,runs,reports,documents,evaluations,samples,approvals,stats,budget,system,user}. Keep it lightweight, no document full text or credential values.
- GET/POST /projects; PUT/DELETE /projects/{id}
- GET/POST /agents; PUT/DELETE /agents/{id}; POST /agents/{id}/test {message,mode}
- GET /skills; POST /skills/{id}/test {query,project_id,document_ids,mode,...}
- GET /documents?project_id=&include_temporary=; POST /documents/upload multipart file, project_id, visibility=(external|local), temporary=(true|false); DELETE /documents/{id}; GET /documents/{id}/chunks; GET /documents/{id}/file. Temporary run-scoped uploads are excluded from library listings and default retrieval, and only participate when explicitly selected via document_ids; they persist so run evidence and report citations stay resolvable.
- POST /knowledge/search {query,project_id,document_ids,limit,semantic}; GET /knowledge/graph?project_id=
- GET/POST /workflows; PUT/DELETE /workflows/{id}; POST /workflows/{id}/validate; POST /workflows/{id}/publish; POST /workflows/{id}/clone; GET /workflows/{id}/versions; POST /workflows/{id}/restore {version}. Deleting a workflow also removes its version snapshots; existing runs keep their immutable workflow_snapshot.
- POST /plan {prompt,project_id,mode} => 202 planning job; GET /planning/{id} returns {status,stage,attempts,workflow_id,error} with real generation/validation progress. Live planning retries repairs up to PLAN_MAX_REPAIRS=2, each carrying validation errors and the previous JSON back to the model.
- POST /runs {workflow_id,project_id,prompt,mode,document_ids}; GET /runs; GET /runs/{id}; POST /runs/{id}/cancel; POST /runs/{id}/retry; POST /runs/{id}/review {decision:approve|reject,feedback,content?}; GET /runs/{id}/events SSE
- GET /reports; GET /reports/{id}; PUT /reports/{id} {content,title}; GET /reports/{id}/versions; GET /reports/{id}/export?format=md|docx|html; POST /reports/{id}/restore {version}
- GET /samples; POST /evaluations {sample_ids,mode,workflow_id?}; GET /evaluations; GET /evaluations/{id}
- GET /audits; GET /settings; PUT /settings (admin, cannot increase budget beyond 300); GET /budget
- GET /health (unauthenticated, no secret details)

## Record fields
Project: id,name,description,category(technology|geography|situational),created_at.
Agent: id,name,role(planner|parser|retriever|writer|coordinator),description,instructions,skill_ids[],model,enabled,version.
Workflow: id,name,description,category,project_id?,version,status(draft|published),nodes[],edges[],source_prompt?,preferred_mode?,created_at,updated_at. Node: {id,type:'task',position:{x,y},data:{label,kind,agent_id?,skill_id?,config:{}}}. Allowed kind: start,parse,retrieve,condition,batch,analyze,review,report,end. Edge: {id,source,target,label?,sourceHandle?}; condition edges use sourceHandle=pass|fail. Config is data, never executable user code. Workflow version snapshots are immutable. source_prompt/preferred_mode record the originating task text and planning mode; the run dialog prefills from them.
Run: id,name,workflow_id,workflow_name,workflow_version,project_id,prompt,mode,status(queued|running|waiting_review|completed|failed|cancelled|interrupted),progress,steps[],logs[],evidence[],report_id?,error?,created_at,updated_at. Step: node_id,label,kind,status,payload?,error?,started_at?,finished_at?.
Report: id,title,category,project_id,run_id,content(markdown),citations[],version,status(draft|reviewed),created_at,updated_at.
Document: id,name,project_id,visibility(external|local),status,chunk_count,size,kind,created_at; internal path and raw text must not leak through bootstrap.
Evidence: id,document_id,document_name,location,text,score. Source IDs must resolve to actual retrieved chunks.
Budget: limit_cny,spent_cny,reserved_cny,remaining_cny,request_count,blocked_count,input_tokens,output_tokens,pricing_note. Values are conservative upper bound from official peak prices, labeled accordingly.
System: default_mode,runtime_mode,model,key_configured,agno_version,skills status,embedding_status.

## Gateway contract (root)
`async model_gateway.complete(prompt, *, system='', purpose='general', run_id=None, max_tokens=2000, json_mode=False, images=None) -> dict` returns {text,usage,cost_cny,model}. Every request is a genuine Agno Agent using the restricted DeepSeek transport. For deterministic rehearsal do not call gateway. Await gateway, no other model paths.
`model_gateway.budget() -> dict`. `model_gateway.config_status() -> dict` safe configuration only.

## Knowledge contract (knowledge agent)
`knowledge.seed()` idempotent via seeds module. `knowledge.skills() -> list`. `knowledge.ingest(filename,content:bytes,project_id,visibility) -> document`. `knowledge.search(query,project_id=None,document_ids=None,limit=6,semantic=False) -> evidence list`. `knowledge.chunks(document_id)`, `knowledge.remove(document_id)`, `knowledge.graph(project_id=None)`, `async knowledge.execute(skill_id,params)`. Define module-level singleton knowledge. Six skills: knowledge_search,graph_query,document_parse,ocr,semantic_search,multimodal. Vision uses gateway only, never raw model SDK.
`seeds.seed_all()` idempotent creates public-safe synthetic samples, 3 projects,4 agent roles,6 workflows,30 task samples,graph fixtures. Avoid importing engine/API. It may import knowledge lazily after base records are saved.
`reports.create_report(run,content,evidence)->dict`, `reports.update_report(id,content,title=None)->dict`, `reports.export_report(id,format)->(bytes,mime,filename)`, `reports.versions(id)` and `reports.restore(id,version)`.

## Execution and security
Backend enforces role: admin all, operator create/edit/run, reviewer read and approve/edit reports. Read all local demo projects. Persist node outputs and events. Resume explicitly after process restart; do not silently rerun ambiguous external actions. Evidence marked local must NEVER reach cloud models, including through prompt context, agent test, or vision. Rehearsal uses actual local retrieval and deterministic formatting, labeled throughout. SSE or polling must survive refreshing the frontend.

Live planning generates nodes, edges and configuration, validates each terminal path's report/review order, and permits metered repair attempts (PLAN_MAX_REPAIRS=2) that feed validation errors and the previous JSON back to the model. Failed responses and validation reasons remain in local planning records and on the visible planning job. Reports are created as drafts before review; rejection records feedback and explicit retry reruns the relevant analysis and downstream nodes. Editing or restoring a report invalidates its reviewed status. A parse node without a bound skill parses the run's selected documents via the local document_parse skill (full content); without an explicit selection it parses up to 5 project documents capped at 4 chunks each and says so in its payload. The metered parser summary (purpose=document_parse_summary) runs only for explicitly selected documents. Review nodes always show auditable content: report draft, else analysis text, else a plainly labeled status note with the evidence collected so far.

Budget reservations use a separate durable SQLite ledger with transactional concurrency control. Unknown response costs remain reserved; cancellation does not refund requests already sent. The user-facing estimate uses twice the verified peak token prices and includes pending reservations. There is no reset endpoint. Tests cover concurrent reservations, unknown costs, network target restrictions, local evidence isolation, cancelled-run persistence, and gzip responses.

Demo limits: seeded graph relations are not automatically extracted from new uploads; scanned PDFs require image extraction before OCR; rule evaluations do not measure factual accuracy. The local accounts and SQLite store are intended for this single-machine demo, with deployment hardening required before multi-user network service.
