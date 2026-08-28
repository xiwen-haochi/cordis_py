"""Cordis Python 运行时共用工具。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Mapping
from typing import Any, TypeVar

from .errors import AsyncRequiredError

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


def has_running_loop() -> bool:
    """当前线程是否运行着事件循环。

    这是同步/异步双模式的分流点：有运行中的事件循环时，生命周期转换
    与异步效果走 ``asyncio`` 后台调度；否则走内联同步驱动。
    """
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def drive_sync(awaitable: Awaitable[Any], where: str = "当前操作") -> None:
    """在没有运行事件循环的线程中内联驱动一个协程。

    同步模式的核心组件：被驱动对象必须是普通协程，且其内部等待链也必须
    是纯协程（例如插件回调、异步效果收集器）。一旦协程挂起在需要事件循环
    服务的等待点（如 ``asyncio.sleep``、``asyncio.create_task``），或者使用了
    事件循环原语而抛出不带运行循环的 ``RuntimeError``，都会抛出
    :class:`AsyncRequiredError`，提示调用方改用异步 API。
    """
    if not inspect.iscoroutine(awaitable):
        raise AsyncRequiredError(where)
    try:
        awaitable.send(None)
    except StopIteration:
        return
    except RuntimeError as exc:
        awaitable.close()
        if "no running event loop" in str(exc):
            raise AsyncRequiredError(where) from exc
        raise
    # 协程挂起：说明有等待点需要事件循环服务。
    awaitable.close()
    raise AsyncRequiredError(where)


def normalize_sync_error(error: BaseException, where: str) -> None:
    """把同步模式收集到的事件循环相关错误转为 AsyncRequiredError 并抛出。

    插件体内使用 ``asyncio.sleep`` 等原语时，异常发生在协程内部，会先被
    fiber 记录为加载错误；在同步入口重新抛出前，用本函数给出清晰的
    双模式边界提示。
    """
    if isinstance(error, RuntimeError) and "no running event loop" in str(error):
        raise AsyncRequiredError(where) from error
    raise error


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
