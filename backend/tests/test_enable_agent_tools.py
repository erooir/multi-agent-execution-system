"""The administrator CLI must not grant new domain tool or skill scopes implicitly."""

from backend.app.platform import service
from backend.app.platform.contracts import AgentInput
from backend.app.platform.tool_registry import BASE_TOOL_IDS, catalog, digest, freeze_bindings
from backend.tests import test_v2_platform as fixtures
from backend.tests.test_v2_platform import ADMIN
from scripts import enable_agent_tools

db = fixtures.db


def published(db, cap, *, stale=False, **changes):
    pid = db.put("projects", {"name": "Synthetic CLI test", "members": [ADMIN["id"]], "status": "active"})[
        "id"
    ]
    definition = next(c for c in service.CAPABILITIES if c["id"] == cap)
    key = f"{pid[:12]}-{cap}"
    body = AgentInput(
        project_id=pid,
        name=definition["name"],
        description=definition["description"],
        capabilities=[cap],
        modalities=definition["modalities"],
        model_policy="cloud",
        tool_ids=[definition["tool_id"]],
    ).model_dump()
    body.update(changes, id=key, status="published", current_version=1, built_in=True)
    if body["tool_refs"]:
        body["runtime_bindings"] = freeze_bindings(body)
        if stale:
            body["runtime_bindings"]["tools"][0]["checksum"] = "prior-template-implementation"
    db.put("agents", body)
    db.put(
        "agent_versions",
        {
            **body,
            "id": key + ":1",
            "definition_id": key,
            "agent_id": key,
            "version": 1,
            "checksum": digest(body),
        },
    )
    return db.get("agents", key)


def test_cli_migrates_only_stock_generic_scope_and_is_idempotent(db, monkeypatch, capsys):
    available = catalog()
    refs = {t["id"]: {"tool_id": t["id"], "version": t["version"]} for t in available["tools"]}
    basic = [r for k, r in refs.items() if k in BASE_TOOL_IDS]
    original_skill_ids = {"evidence-analysis", "table-analysis"}
    skills = [
        {"skill_id": s["id"], "version": s["version"]}
        for s in available["skills"]
        if s["id"] in original_skill_ids
    ]
    assert len(basic) == 6 and len(refs) > len(basic)
    stock = published(db, "text_analysis", modalities=["text"])
    old_template = published(
        db, "source_summary", stale=True, execution_strategy="tool_agent", tool_refs=basic, skill_refs=skills
    )
    original = {a["id"]: db.get("agent_versions", a["id"] + ":1") for a in (stock, old_template)}
    narrow = published(
        db, "source_summary", execution_strategy="tool_agent", tool_refs=[refs["data.read_source"]]
    )
    domain = published(
        db, "source_summary", execution_strategy="tool_agent", tool_refs=[refs["series.baseline_anomaly"]]
    )
    limited_skills = published(
        db, "source_summary", execution_strategy="tool_agent", tool_refs=basic, skill_refs=[]
    )
    limited_modalities = published(
        db,
        "source_summary",
        execution_strategy="tool_agent",
        tool_refs=basic,
        skill_refs=skills,
        modalities=["text"],
    )
    local = published(db, "source_summary", model_policy="local")
    monkeypatch.setattr(enable_agent_tools, "repo", db)
    monkeypatch.setattr(enable_agent_tools.auth, "list_users", lambda: [{**ADMIN, "enabled": True}])
    enable_agent_tools.main()
    assert set(capsys.readouterr().out.splitlines()) == {f"Published {key} v2" for key in original}
    for aid, old in original.items():
        current = db.get("agents", aid)
        assert current["current_version"] == 2 and current["execution_strategy"] == "tool_agent"
        assert {r["tool_id"] for r in current["tool_refs"]} == BASE_TOOL_IDS
        assert {t["id"] for t in current["runtime_bindings"]["tools"]} == BASE_TOOL_IDS
        assert {r["skill_id"] for r in current["skill_refs"]} == original_skill_ids
        assert {r["id"] for r in current["runtime_bindings"]["skills"]} == original_skill_ids
        assert db.get("agent_versions", aid + ":1") == old
    for custom in [narrow, domain, limited_skills, limited_modalities, local]:
        assert db.get("agents", custom["id"]) == custom
        assert len(db.list("agent_versions", definition_id=custom["id"])) == 1
    before = {k: db.list(k) for k in ("agents", "agent_versions", "audits")}
    enable_agent_tools.main()
    assert not capsys.readouterr().out
    assert before == {k: db.list(k) for k in before}
