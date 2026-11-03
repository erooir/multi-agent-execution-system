"""Independent MCP fixture used only by integration tests."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("native-external-fixture")


@server.tool()
def echo_sum(a: int, b: int) -> dict:
    """Add two synthetic integers and return structured content."""
    return {"total": a + b, "fixture": "external"}


if __name__ == "__main__":
    server.run(transport="stdio")
