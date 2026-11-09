"""One lock order for shared mutable dependencies of frozen task snapshots."""


def lock_snapshot_dependencies(repository, snapshots, *, include_agents=False):
    """Lock all dependencies across the batch before verifying any one sample.

    Callers lock their target workflow/agent definition before entering here.
    Runtime execution does not lock definitions: its versions are immutable.
    The snapshot lists themselves keep their business and presentation order.
    Read-only checks outside a transaction deliberately acquire no row locks.
    """
    if repository.connection is None:
        return
    snapshots = tuple(snapshots)
    groups = []
    if include_agents:
        groups.append(
            (
                "agents",
                {
                    agent.get("agent_id", agent["id"])
                    for snap in snapshots
                    for agent in snap.get("agents", [])
                },
            )
        )
    groups.extend(
        [
            ("data_assets", {asset["id"] for snap in snapshots for asset in snap.get("inputs", [])}),
            (
                "capability_definitions",
                {snap["task_plugin"]["id"] for snap in snapshots if snap.get("task_plugin")},
            ),
        ]
    )
    for kind, identifiers in groups:
        for identifier in sorted(identifiers):
            repository.get(kind, identifier, for_update=True)
