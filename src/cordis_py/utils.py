"""Cordis Python 运行时共用工具。"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from typing import Any, TypeVar

T = TypeVar("T")

Disposable = Callable[[], Any]
Effect = (
    Disposable
    | Iterable[Disposable]
    | AsyncIterable[Disposable]
    | None
)
Inject = list[str] | Mapping[str, Any] | None


def is_async_callable(obj: Any) -> bool:
    """判断 *obj* 是否为异步函数或具有异步 __call__ 的对象。"""
    if inspect.iscoroutinefunction(obj):
        return True
    if callable(obj):
        return inspect.iscoroutinefunction(obj.__call__)
    return False


def maybe_await(value: Any) -> Any:
    """如果 *value* 可等待则返回其自身，否则原样返回。"""
    if inspect.isawaitable(value):
        return value
    return value


async def await_maybe(value: Any) -> Any:
    """如果 *value* 可等待则等待它，否则原样返回。"""
    if inspect.isawaitable(value):
        return await value
    return value


def resolve_inject(plugin: Any) -> dict[str, Any]:
    """将插件的 ``inject`` 元数据规范化为“名称 -> 配置”映射。"""
    raw = getattr(plugin, "inject", None)
    if raw is None:
        return {}
    if isinstance(raw, list):
        return {name: None for name in raw}
    if isinstance(raw, Mapping):
        return dict(raw)
    raise TypeError(f"invalid inject declaration: {raw!r}")


def collect_disposers(effect: Effect) -> list[Disposable]:
    """从支持的效果返回值中收集 disposer（仅同步）。

    异步可迭代对象由 fiber/context 调用方处理，因为需要异步事件循环。
    """
    if effect is None:
        return []
    if callable(effect):
        return [effect]
    if isinstance(effect, Iterable) and not isinstance(effect, (str, bytes)):
        return [item for item in effect if callable(item)]
    return []
