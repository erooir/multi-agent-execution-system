"""aviation-local MCP Server：把 OurAirports 离线查询暴露为 MCP 工具（stdio）。

运行：uv run python -m backend.app.mcp_servers.aviation_server（工作目录为仓库根）。
实现复用 capabilities.tools.airports，保持与本地 Tool 完全一致的行为。
"""

from __future__ import annotations

from fastmcp import FastMCP

from ..capabilities.tools import airports

mcp = FastMCP("aviation-local")


@mcp.tool()
def lookup_airport(query: str, limit: int = 8) -> dict:
    """按 ICAO/IATA/名称/城市模糊查询机场（OurAirports 离线快照）。"""
    return airports.lookup_airport(query=query, limit=limit)


@mcp.tool()
def nearby_airports(latitude: float, longitude: float, radius_km: float = 100.0, limit: int = 20) -> dict:
    """按经纬度与半径查询附近机场（OurAirports 离线快照）。"""
    return airports.nearby_airports(latitude=latitude, longitude=longitude, radius_km=radius_km, limit=limit)


if __name__ == "__main__":
    mcp.run()
