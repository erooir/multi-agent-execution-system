"""An authorized local operation preview. No task, model request, or unapproved tool is created."""

from . import service as s
from .domain_tool import FrozenSourceReader, dispatch, operation_catalog
from .runtime import TaskFailure
from .tool_registry import digest


def preview(repository, user, agent_id, tool_id, body):
    op = operation_catalog().get(tool_id)
    agent = s.required("agents", agent_id, user, True, repository)
    if op is None:
        s.fail("unsupported_tool_preview", "此工具未登记本地试用入口", 422)
    spec = op.spec()
    project_id = agent["project_id"]
    if len(set(body.input_refs)) != len(body.input_refs):
        s.fail("duplicate_inputs", "试用来源不能重复", 422)

    def check(tx):
        current = s.required("agents", agent_id, user, True, tx)
        if current["status"] != "published" or current["current_version"] != body.version:
            s.fail("unavailable_agent_version", "试用需要启用的当前发布版本", 409)
        version = tx.get("agent_versions", f"{agent_id}:{body.version}")
        if not version or version["project_id"] != project_id:
            s.fail("unavailable_agent_version", "找不到该智能体的冻结版本", 409)
        grants = (version.get("runtime_bindings") or {}).get("tools", [])
        ref = {"tool_id": tool_id, "version": spec["version"]}
        role = version.get("role_contract")
        if role is not None and tool_id not in role.get("grants", []):
            s.fail("tool_not_authorized", "冻结岗位合同未授权该工具", 403)
        if ref not in version.get("tool_refs", []) or not any(item["id"] == tool_id for item in grants):
            s.fail("tool_not_authorized", "此智能体版本未获得该工具授权", 403)
        if spec not in grants:
            s.fail("capability_version_changed", "工具实现与冻结版本不符", 409)
        for asset in inputs:
            if asset["modality"] not in version.get("modalities", []):
                s.fail("modality_mismatch", "资料模态不在该智能体版本的授权范围内", 422)
            s.verify_frozen_asset(asset, tx.get("data_assets", asset["id"]))

    inputs = []
    with repository.transaction() as tx:
        for key in body.input_refs:
            asset = s.required("data_assets", key, user, repository=tx)
            if asset["project_id"] != project_id:
                s.fail("permission_denied", "试用资料须属于智能体项目", 403)
            inputs.append(asset)
        check(tx)

    def checkpoint():
        with repository.transaction() as tx:
            check(tx)

    reader = FrozenSourceReader(inputs, checkpoint)
    try:
        result = dispatch(tool_id, body.arguments, reader)
    except TaskFailure as exc:
        s.fail(exc.code, exc.message, 403 if exc.code == "tool_source_denied" else 409)
    except (TypeError, ValueError, KeyError, OverflowError):
        s.fail("invalid_tool_input", "工具参数、数据格式或计算范围不满足要求", 422)
    checksum = digest(result)
    refs = sorted(reader.source_refs)
    with repository.transaction() as tx:
        check(tx)
        if op.spec() != spec:
            s.fail("capability_version_changed", "试用期间工具实现发生变化，结果已屏蔽", 409)
        s.audit(
            tx,
            project_id,
            "tool.preview",
            agent_id,
            user,
            {
                "tool_id": tool_id,
                "tool_version": spec["version"],
                "agent_version": body.version,
                "source_refs": refs,
                "arguments_checksum": digest(body.arguments),
                "result_checksum": checksum,
            },
        )
    return {
        "tool_id": tool_id,
        "version": spec["version"],
        "agent_version": body.version,
        "source_refs": refs,
        "result": result,
        "checksum": checksum,
    }
