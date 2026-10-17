"""Publish new built-in versions without editing released definitions or user customizations."""

from backend.app import auth
from backend.app.platform import service
from backend.app.platform.contracts import AgentInput
from backend.app.platform.db import repo
from backend.app.platform.tool_registry import catalog, freeze_bindings


def enable(repository, user):
    available = catalog()
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
        if definition.get("instructions") or definition.get("parameters"):
            continue
        fields = AgentInput.model_fields
        body = AgentInput.model_validate(
            {key: value for key, value in definition.items() if key in fields}
        ).model_dump()
        body.update(
            execution_strategy="tool_agent",
            modalities=["text", "structured"],
            model_policy="cloud",
            tool_refs=[{"tool_id": item["id"], "version": item["version"]} for item in available["tools"]],
            skill_refs=[{"skill_id": item["id"], "version": item["version"]} for item in available["skills"]],
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


if __name__ == "__main__":
    main()
