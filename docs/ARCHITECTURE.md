# Research Agent Workbench Architecture

## Runtime
Python 3.12+, FastAPI, Agno AgentOS, SQLite. React/TypeScript/Vite/React Flow frontend. Application data and credentials are under ignored `.local/`. No cloud control plane subscription. All model traffic uses the budget gateway.

## Ownership during initial parallel implementation
- Root: storage.py, config.py, model_gateway.py, auth.py, dependency manifests, scripts, integration and verification.
- Engine agent: engine.py, api.py, main.py, workflows.py, engine/API tests.
- Knowledge agent: knowledge.py, seeds.py, reports.py, knowledge tests. No edits to other owned files.
- Frontend agent: frontend/** only.

## Storage contract
`from .storage import store` singleton. `store.list(kind) -> list[dict]`, `store.get(kind,id) -> dict|None`, `store.save(kind,item) -> dict` (generates id/timestamps when omitted), `store.delete(kind,id) -> bool`, `store.audit(action,entity,detail,user='system')`. Generic JSON records, all changes atomic. Kinds: projects, agents, workflows, runs, reports, documents, chunks, evaluations, samples, events, approvals, graph_nodes, graph_edges, audits.

## API contract
All routes under `/api`. JSON collections are arrays (no envelope). Errors return HTTP errors with `detail`. Model is `deepseek-flash`, with a visibly labeled deterministic `rehearsal` execution mode, never silent fallback.

- POST /auth/login {username,password}; GET /auth/me; POST /auth/logout. Session is HttpOnly cookie. Seed demo usernames admin/operator/reviewer, password `demo12345` (localhost-only demo accounts).
- GET /bootstrap returns {projects,agents,skills,workflows,runs,reports,documents,evaluations,samples,approvals,stats,budget,system,user}. Keep it lightweight, no document full text or credential values.
- GET/POST /projects; PUT/DELETE /projects/{id}
- GET/POST /agents; PUT/DELETE /agents/{id}; POST /agents/{id}/test {message,mode}
- GET /skills; POST /skills/{id}/test {query,project_id,document_ids,mode,...}
- GET /documents?project_id=; POST /documents/upload multipart file, project_id, visibility=(external|local); DELETE /documents/{id}; GET /documents/{id}/chunks; GET /documents/{id}/file
- POST /knowledge/search {query,project_id,document_ids,limit,semantic}; GET /knowledge/graph?project_id=
- GET/POST /workflows; PUT /workflows/{id}; POST /workflows/{id}/validate; POST /workflows/{id}/publish; POST /workflows/{id}/clone; GET /workflows/{id}/versions; POST /workflows/{id}/restore {version}
- POST /plan {prompt,project_id,mode} => workflow object (saved draft).
- POST /runs {workflow_id,project_id,prompt,mode,document_ids}; GET /runs; GET /runs/{id}; POST /runs/{id}/cancel; POST /runs/{id}/retry; POST /runs/{id}/review {decision:approve|reject,feedback,content?}; GET /runs/{id}/events SSE
- GET /reports; GET /reports/{id}; PUT /reports/{id} {content,title}; GET /reports/{id}/versions; GET /reports/{id}/export?format=md|docx|html; POST /reports/{id}/restore {version}
- GET /samples; POST /evaluations {sample_ids,mode,workflow_id?}; GET /evaluations; GET /evaluations/{id}
- GET /audits; GET /settings; PUT /settings (admin, cannot increase budget beyond 300); GET /budget
- GET /health (unauthenticated, no secret details)

## Record fields
Project: id,name,description,category(technology|geography|situational),created_at.
Agent: id,name,role(planner|retriever|writer|coordinator),description,instructions,skill_ids[],model,enabled,version.
Workflow: id,name,description,category,project_id?,version,status(draft|published),nodes[],edges[],created_at,updated_at. Node: {id,type:'task',position:{x,y},data:{label,kind,agent_id?,skill_id?,config:{}}}. Allowed kind: start,parse,retrieve,condition,batch,analyze,review,report,end. Edge: {id,source,target,label?,sourceHandle?}; condition edges use sourceHandle=pass|fail. Config is data, never executable user code. Workflow version snapshots are immutable.
Run: id,name,workflow_id,workflow_name,workflow_version,project_id,prompt,mode,status(queued|running|waiting_review|completed|failed|cancelled|interrupted),progress,steps[],logs[],evidence[],report_id?,error?,created_at,updated_at. Step: node_id,label,kind,status,payload?,error?,started_at?,finished_at?.
Report: id,title,category,project_id,run_id,content(markdown),citations[],version,status(draft|reviewed),created_at,updated_at.
Document: id,name,project_id,visibility(external|local),status,chunk_count,size,kind,created_at; internal path and raw text must not leak through bootstrap.
Evidence: id,document_id,document_name,location,text,score. Source IDs must resolve to actual retrieved chunks.
Budget: limit_cny,spent_cny,reserved_cny,remaining_cny,request_count,blocked_count,input_tokens,output_tokens,pricing_note. Values are conservative upper bound from official peak prices, labeled accordingly.
System: mode,model,key_configured,agno_version,skills status,embedding_status.

## Gateway contract (root)
`async model_gateway.complete(prompt, *, system='', purpose='general', run_id=None, max_tokens=2000, json_mode=False, images=None) -> dict` returns {text,usage,cost_cny,model}. Every request is a genuine Agno Agent using the restricted DeepSeek transport. For deterministic rehearsal do not call gateway. Await gateway, no other model paths.
`model_gateway.budget() -> dict`. `model_gateway.config_status() -> dict` safe configuration only.

## Knowledge contract (knowledge agent)
`knowledge.seed()` idempotent via seeds module. `knowledge.skills() -> list`. `knowledge.ingest(filename,content:bytes,project_id,visibility) -> document`. `knowledge.search(query,project_id=None,document_ids=None,limit=6,semantic=False) -> evidence list`. `knowledge.chunks(document_id)`, `knowledge.remove(document_id)`, `knowledge.graph(project_id=None)`, `async knowledge.execute(skill_id,params)`. Define module-level singleton knowledge. Six skills: knowledge_search,graph_query,document_parse,ocr,semantic_search,multimodal. Vision uses gateway only, never raw model SDK.
`seeds.seed_all()` idempotent creates public-safe synthetic samples, 3 projects,4 agent roles,6 workflows,30 task samples,graph fixtures. Avoid importing engine/API. It may import knowledge lazily after base records are saved.
`reports.create_report(run,content,evidence)->dict`, `reports.update_report(id,content,title=None)->dict`, `reports.export_report(id,format)->(bytes,mime,filename)`, `reports.versions(id)` and `reports.restore(id,version)`.

## Execution and security
Backend enforces role: admin all, operator create/edit/run, reviewer read and approve/edit reports. Read all local demo projects. Persist node outputs and events. Resume explicitly after process restart; do not silently rerun ambiguous external actions. Evidence marked local must NEVER reach cloud models, including through prompt context, agent test, or vision. Rehearsal uses actual local retrieval and deterministic formatting, labeled throughout. SSE or polling must survive refreshing the frontend.
