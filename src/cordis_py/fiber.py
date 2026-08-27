"""Fiber：插件/组件的一次运行实例。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from enum import Enum
from typing import TYPE_CHECKING, Any

from .errors import InactiveEffect, InvalidEffect
from .utils import Disposable, Effect, await_maybe

if TYPE_CHECKING:
    from .context import Context


class FiberState(str, Enum):
    """fiber 的生命周期状态。"""

    PENDING = "pending"
    LOADING = "loading"
    ACTIVE = "active"
    FAILED = "failed"
    UNLOADING = "unloading"
    DISPOSED = "disposed"


async def _collect_async_effect(result: Any) -> list[Disposable]:
    """将支持的效果返回值转换为 disposer 列表。"""
    if result is None:
        return []
    if callable(result):
        return [result]
    if isinstance(result, AsyncIterable):
        out: list[Disposable] = []
        async for item in result:
            if not callable(item):
                raise InvalidEffect(item)
            out.append(item)
        return out
    if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
        out = []
        for item in result:
            if not callable(item):
                raise InvalidEffect(item)
            out.append(item)
        return out
    raise InvalidEffect(result)


def _collect_sync_effect(result: Any) -> list[Disposable]:
    """将同步效果返回值转换为 disposer 列表。"""
    if result is None:
        return []
    if callable(result):
        return [result]
    if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
        out = []
        for item in result:
            if not callable(item):
                raise InvalidEffect(item)
            out.append(item)
        return out
    raise InvalidEffect(result)


async def _run_disposers(disposers: list[Disposable]) -> None:
    for disposer in reversed(disposers):
        try:
            result = disposer()
            if inspect.isawaitable(result):
                await result
        except Exception:
            # 保证清理过程有韧性：单个 disposer 失败不能阻止其他清理。
            continue


class Fiber:
    """带有可追踪效果和依赖生命周期的插件实例。"""

    def __init__(
        self,
        ctx: Context,
        plugin: Any,
        config: Any,
        inject: dict[str, Any] | None = None,
        *,
        uid: int = 0,
        is_root: bool = False,
    ) -> None:
        self.ctx = ctx
        self.plugin = plugin
        self.config = config
        self.inject = inject or {}
        self.uid = uid
        self.is_root = is_root
        self.state = FiberState.ACTIVE if is_root else FiberState.PENDING
        self.target: tuple[str, ...] | None = None
        self.committed: dict[str, Any] = {}
        self.store: set[str] = set()
        self.parent_fiber: Fiber | None = None
        self._effects: list[Disposable] = []
        self._disposed = False
        self._error: Exception | None = None
        self._inertia: asyncio.Task[None] | None = None
        self._effect_once: set[int] = set()

    @property
    def name(self) -> str:
        if self.plugin is None:
            return "root"
        return getattr(self.plugin, "name", None) or getattr(self.plugin, "__name__", type(self.plugin).__name__)

    def assert_active(self) -> None:
        if self._disposed:
            raise InactiveEffect()

    def add_effect(self, disposer: Disposable) -> None:
        self.assert_active()
        self._effects.append(disposer)

    def effect(self, callback: Callable[[], Effect], label: str = "anonymous") -> Disposable:
        """在此 fiber 上注册可逆效果。

        回调可以返回 disposer、disposer 可迭代对象、异步 disposer 可迭代对象或 ``None``。
        返回的可调用对象会按 LIFO 顺序清理所有已收集的 disposer。
        异步效果会在插件继续运行的同时由后台任务收集。
        """
        self.assert_active()
        result = callback()

        if inspect.isawaitable(result):
            holder: list[Disposable] = []

            async def consume_awaitable() -> None:
                resolved = await result
                holder.extend(await _collect_async_effect(resolved))

            task = asyncio.create_task(consume_awaitable())

            async def dispose_async() -> None:
                await task
                await _run_disposers(holder)

            self._effects.append(dispose_async)
            return dispose_async

        if isinstance(result, AsyncIterable):
            holder = []

            async def consume_async_iter() -> None:
                async for item in result:
                    if not callable(item):
                        raise InvalidEffect(item)
                    holder.append(item)

            task = asyncio.create_task(consume_async_iter())

            async def dispose_async_iter() -> None:
                await task
                await _run_disposers(holder)

            self._effects.append(dispose_async_iter)
            return dispose_async_iter

        disposers = _collect_sync_effect(result)
        if not disposers:
            def noop() -> None:
                return None
            return noop

        async def dispose() -> None:
            if id(dispose) in self._effect_once:
                return
            self._effect_once.add(id(dispose))
            await _run_disposers(disposers)

        self._effects.append(dispose)
        return dispose

    async def _invoke_plugin(self) -> Any:
        plugin = self.plugin
        if plugin is None:
            return None
        if inspect.isclass(plugin):
            from .service import Service

            if issubclass(plugin, Service):
                return plugin(self.ctx)
            return plugin(self.ctx, self.config)
        if callable(plugin):
            return await await_maybe(plugin(self.ctx, self.config))
        apply = getattr(plugin, "apply", None)
        if callable(apply):
            return await await_maybe(apply(self.ctx, self.config))
        raise TypeError(f"invalid plugin: {plugin!r}")

    def _register_plugin_result(self, result: Any) -> None:
        if result is None:
            return
        if callable(result):
            self.add_effect(result)
            return
        if isinstance(result, AsyncIterable):
            raise TypeError("plugins may not return async iterables directly; use ctx.effect() instead")
        if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
            for item in result:
                if callable(item):
                    self.add_effect(item)
            return

    def _compute_target(self) -> tuple[str, ...] | None:
        if not self.inject:
            return ()
        for name in self.inject:
            if not self.ctx._has_active_service(name):
                return None
        return tuple(sorted(self.inject))

    def _schedule(self, coro: Awaitable[None]) -> None:
        task = asyncio.create_task(coro)
        self._inertia = task

        def _done(t: asyncio.Task[None]) -> None:
            if self._inertia is t:
                self._inertia = None

        task.add_done_callback(_done)

    def refresh(self) -> None:
        """重新计算依赖目标，并在需要时安排加载/卸载。"""
        if self.is_root or self._disposed:
            return
        target = self._compute_target()
        if target == self.target:
            return
        self.target = target
        if self._inertia is not None:
            return
        if target is not None:
            self._schedule(self._reload())
        else:
            self._schedule(self._unload())

    async def _reload(self) -> None:
        self.state = FiberState.LOADING
        target0 = self.target
        self.committed = {
            name: self.ctx._get_active_service_value(name)
            for name in self.inject
            if self.ctx._has_active_service(name)
        }
        try:
            result = await self._invoke_plugin()
            self._register_plugin_result(result)
            self._error = None
            if not self._disposed and self.target == target0:
                self.state = FiberState.ACTIVE
                self.ctx._notify_provided(self.store)
            else:
                self.target = None
                await self._unload()
        except Exception as exc:
            self._error = exc
            self.committed = {}
            await self._unload()
            self.state = FiberState.FAILED

    async def _unload(self) -> None:
        if self._disposed and self.state == FiberState.DISPOSED:
            return
        self.state = FiberState.UNLOADING
        self.ctx.root._remove_fiber_services(self)
        self.committed = {}
        disposers = list(self._effects)
        self._effects.clear()
        await _run_disposers(disposers)
        if self._disposed:
            self.state = FiberState.DISPOSED
        else:
            self.target = None
            self.state = FiberState.PENDING

    async def wait(self) -> Fiber:
        """等待当前生命周期转换完成。"""
        if self._inertia is not None:
            await self._inertia
        if self._error is not None and not self._disposed:
            raise self._error
        return self

    def __await__(self):
        return self.wait().__await__()

    async def dispose(self) -> None:
        """卸载此 fiber 及其所有效果。"""
        if self.is_root:
            await self.ctx.dispose_all()
            return
        if self._disposed:
            return
        self._disposed = True
        self.target = None
        if self._inertia is not None:
            await self._inertia
        children = [
            child
            for child in list(self.ctx.root._fibers)
            if child.parent_fiber is self
        ]
        for child in children:
            await child.dispose()
        await self._unload()
        self.ctx.root._remove_fiber(self)

    async def restart(self) -> None:
        """清理当前效果；若依赖仍满足则重新加载。"""
        self.assert_active()
        self.target = None
        self.refresh()
        await self.wait()

    async def update(self, config: Any) -> None:
        """应用新配置并重启 fiber。"""
        self.assert_active()
        self.config = config
        self.target = None
        self.refresh()
        await self.wait()


__all__ = ["Fiber", "FiberState"]
