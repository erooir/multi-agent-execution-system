"""embedding 模型状态与向量缓存服务。

模型状态由 Knowledge 实例持有（保证现有测试对 _get_embedding 的 monkeypatch
继续生效）；本模块提供状态字典组装与向量缓存的显式实现供新内核使用。
"""

from __future__ import annotations

import hashlib
from typing import Any

EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


def status_dict(state: str, error: str | None) -> dict:
    return {
        "status": state,
        "model": EMBEDDING_MODEL,
        "device": "CPU",
        "error": error,
        "note": "首次调用时下载公开模型到本机；仅模型下载联网，文档嵌入始终在本地运行",
    }


class VectorCache:
    """按 chunk id 缓存（文本哈希, 向量），文本变化时自动失效。"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, Any]] = {}

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def get_fresh(self, chunk_id: str, text: str):
        entry = self._cache.get(chunk_id)
        if entry and entry[0] == self.digest(text):
            return entry[1]
        return None

    def put(self, chunk_id: str, text: str, vector: Any) -> None:
        self._cache[chunk_id] = (self.digest(text), vector)

    def drop(self, chunk_id: str) -> None:
        self._cache.pop(chunk_id, None)
