"""Publish new built-in versions without editing released definitions or user customizations."""

from backend.app import auth
from backend.app.platform import service
from backend.app.platform.contracts import AgentInput
from backend.app.platform.db import repo
from backend.app.platform.tool_registry import BASE_TOOL_IDS, catalog, freeze_bindings

STOCK_SKILL_IDS = frozenset({"evidence-analysis", "table-analysis"})


def enable(repository, user):
    available = catalog(include_readiness=False)
    # Expanding the product catalog must never expand the legacy template's authorization.
    stock_skills = STOCK_SKILL_IDS
    skills = [item for item in available["skills"] if item["id"] in stock_skills]
    changed = []
    for definition in repository.list("agents"):
        if (
            not definition.get("built_in")
            or definition["status"] != "published"
            or definition.get("structure") != "atomic"
            or len(definition["capabilities"]) != 1
            or definition["capabilities"][0] not in {"text_analysis", "source_summary"}
        ):
            continue
        capability = definition["capabilities"][0]
        if (
            definition.get("instructions")
            or definition.get("parameters")
            or definition.get("model_policy") != "cloud"
            or definition.get("purpose", "general") != "general"
            or definition.get("tool_ids") != [capability]
        ):
            continue
        if definition.get("execution_strategy", "fixed") == "fixed":
            # Before tool bindings existed, the text_analysis seed accepted text alone.
            if (
                definition["current_version"] != 1
                or definition.get("tool_refs")
                or definition.get("skill_refs")
                or (
                    set(definition["modalities"]) != {"text", "structured"}
                    and not (capability == "text_analysis" and definition["modalities"] == ["text"])
                )
            ):
                continue
        elif (
            set(definition["modalities"]) != {"text", "structured"}
            or {ref["tool_id"] for ref in definition.get("tool_refs", [])} != BASE_TOOL_IDS
            or {ref["skill_id"] for ref in definition.get("skill_refs", [])} != stock_skills
        ):
            continue
        fields = AgentInput.model_fields
        body = AgentInput.model_validate(
            {key: value for key, value in definition.items() if key in fields}
        ).model_dump()
        body.update(
            execution_strategy="tool_agent",
            modalities=["text", "structured"],
            model_policy="cloud",
            tool_refs=[
                {"tool_id": item["id"], "version": item["version"]}
                for item in available["tools"]
                if item["id"] in BASE_TOOL_IDS
            ],
            skill_refs=[{"skill_id": item["id"], "version": item["version"]} for item in skills],
        )
        if (
            definition.get("runtime_bindings") == freeze_bindings(body)
            and definition.get("execution_strategy") == "tool_agent"
        ):
            continue
        # Lock and recheck in one outer transaction: concurrent edits are never overwritten.
        with repository.transaction() as tx:
            current = service.required("agents", definition["id"], user, True, tx)
            if current != definition:
                continue
            service.save_agent(body, user, definition["id"], tx)
            published = service.agent_action(definition["id"], "publish", user, tx)
            changed.append({"agent_id": published["id"], "version": published["current_version"]})
            service.audit(tx, definition["project_id"], "agent.tools_enabled", definition["id"], user)
    return changed


def main():
    administrator = next(
        (user for user in auth.list_users() if user["role"] == "admin" and user["enabled"]), None
    )
    if not administrator:
        raise SystemExit("需要一个有效管理员账号")
    for record in enable(repo, administrator):
        print(f"Published {record['agent_id']} v{record['version']}")
    projects = repo.list("projects")
    with repo.transaction() as tx:
        for project in projects:
            service.seed_templates(tx, project["id"])


if __name__ == "__main__":
    main()
