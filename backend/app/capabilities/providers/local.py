"""Local Provider：动态导入 "module:func" 形式的 Python callable 并调用。

同步函数在线程池中执行，避免阻塞事件循环；函数必须是显式参数的纯实现。
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from typing import Any

from ..contracts import ExecutionContext, ToolDefinition
from ..errors import PROVIDER_UNAVAILABLE, CapabilityError


def resolve_callable(entrypoint: str):
    module_name, _, func_name = entrypoint.partition(":")
    if not module_name or not func_name:
        raise CapabilityError(PROVIDER_UNAVAILABLE, f"非法的 entrypoint: {entrypoint!r}")
    try:
        module = importlib.import_module(module_name)
        target = getattr(module, func_name)
    except (ImportError, AttributeError) as error:
        raise CapabilityError(
            PROVIDER_UNAVAILABLE, f"无法加载 entrypoint {entrypoint}: {type(error).__name__}"
        ) from error
    if not callable(target):
        raise CapabilityError(PROVIDER_UNAVAILABLE, f"entrypoint 不是可调用对象: {entrypoint}")
    return target


class LocalProvider:
    async def invoke(
        self, definition: ToolDefinition, arguments: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        target = resolve_callable(definition.entrypoint)
        if inspect.iscoroutinefunction(target):
            result = await target(**arguments)
        else:
            result = await asyncio.to_thread(target, **arguments)
        return result
