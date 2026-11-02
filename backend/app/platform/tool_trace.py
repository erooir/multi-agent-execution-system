"""Attach tool calls directly to the node instance just allocated by the leased worker."""

from .domain_session import DomainToolSession
from .tool_registry import validate_frozen
from .tool_runtime import LocalMCP, ToolSession


class TaskToolTrace:
    def __init__(self, execution, revision, instance_id):
        if not instance_id or not execution.active_node_id:
            raise ValueError("missing_tool_node_instance")
        self.execution = execution
        self.node_id = execution.active_node_id
        self.revision = revision
        self.instance_id = instance_id

    def check_record(self, record):
        if (
            record["task_id"] != self.execution.task["id"]
            or record["node_id"] != self.node_id
            or record["revision"] != self.revision
            or record["instance_id"] != self.instance_id
        ):
            raise ValueError("tool_node_instance_mismatch")

    def save(self, record, event_type, arguments=None, result=None):
        self.check_record(record)
        # Reuse the versioned writer: its transaction checks the worker's lease token
        # before appending the call, event and optional private result checkpoint.
        ToolSession.save(self, record, event_type, arguments=arguments, result=result)


class NodeDomainToolSession(DomainToolSession):
    """Budgeted Agno tools share the known node identity and original authorization rules."""

    def __init__(self, execution, agent, revision, permissions, instance_id):
        self.trace = TaskToolTrace(execution, revision, instance_id)
        self.execution, self.agent, self.revision = execution, agent, revision
        self.instance_id = instance_id
        validate_frozen(agent)
        frozen = agent["runtime_bindings"]
        self.tools = {
            item["name"]: item
            for item in frozen["tools"]
            if f"tool:{item['id']}@{item['version']}" in permissions
        }
        self.skills = {
            item["id"]: item
            for item in frozen["skills"]
            if f"skill:{item['id']}@{item['version']}" in permissions
        }
        self.sources_read = set()
        self.derived = []
        self.mcp = LocalMCP()

    def save(self, record, event_type, arguments=None, result=None):
        self.trace.check_record(record)
        super().save(record, event_type, arguments=arguments, result=result)
