"""Attach tool calls directly to the node instance just allocated by the leased worker."""

from .domain_session import DomainToolSession
from .tool_runtime import ToolSession


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
        super().__init__(execution, agent, revision, permissions, instance_id=instance_id)

    def save(self, record, event_type, arguments=None, result=None):
        self.trace.check_record(record)
        super().save(record, event_type, arguments=arguments, result=result)
