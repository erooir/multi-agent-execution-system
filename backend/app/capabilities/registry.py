"""Skill / Tool / MCP 三层注册表。"""

from __future__ import annotations

from .contracts import McpServerDefinition, SkillManifest, ToolDefinition
from .errors import CAPABILITY_NOT_FOUND, CapabilityError


class _Registry:
    label = "capability"

    def __init__(self) -> None:
        self._items: dict[str, object] = {}

    def register(self, item) -> None:
        existing = self._items.get(item.id)
        if existing is not None:
            raise ValueError(
                f"{self.label} 重复注册: {item.id}（已存在版本 {existing.version}，重复版本 {item.version}）"
            )
        self._items[item.id] = item

    def upsert(self, item) -> None:
        """MCP 刷新发现等场景下替换同 ID 条目；启动期加载仍使用 register。"""
        self._items[item.id] = item

    def unregister(self, item_id: str) -> bool:
        return self._items.pop(item_id, None) is not None

    def get(self, item_id: str):
        item = self._items.get(item_id)
        if item is None:
            raise CapabilityError(CAPABILITY_NOT_FOUND, f"{self.label} 不存在: {item_id}")
        return item

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._items

    def list(self) -> list:
        return list(self._items.values())

    def ids(self) -> list[str]:
        return sorted(self._items)


class SkillRegistry(_Registry):
    label = "Skill"

    def register(self, item: SkillManifest) -> None:
        super().register(item)

    def get(self, item_id: str) -> SkillManifest:
        return super().get(item_id)


class ToolRegistry(_Registry):
    label = "Tool"

    def register(self, item: ToolDefinition) -> None:
        super().register(item)

    def get(self, item_id: str) -> ToolDefinition:
        return super().get(item_id)


class McpRegistry(_Registry):
    label = "MCP Server"

    def register(self, item: McpServerDefinition) -> None:
        super().register(item)

    def get(self, item_id: str) -> McpServerDefinition:
        return super().get(item_id)
