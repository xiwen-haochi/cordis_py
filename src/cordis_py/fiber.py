"""Fiber：插件/组件的一次运行实例。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from enum import Enum
from typing import TYPE_CHECKING, Any

from .errors import AsyncRequiredError, InactiveEffect, InvalidEffect
from .utils import (
    Disposable,
    Effect,
    await_maybe,
    drive_sync,
    has_running_loop,
    normalize_sync_error,
    resolve_plugin_config,
)

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
        except Exception:  # noqa: BLE001, S112
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
        requirements: dict[str, list[Any]] | None = None,
        *,
        uid: int = 0,
        is_root: bool = False,
    ) -> None:
        self.ctx = ctx
        self.plugin = plugin
        self.config = config
        self.inject = inject or {}
        self.requirements = requirements or {}
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
        self._unsatisfied: dict[str, str] = {}

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

            async def dispose_async() -> None:
                await _run_disposers(holder)

            if has_running_loop():
                task = asyncio.create_task(consume_awaitable())

                async def dispose_with_task() -> None:
                    await task
                    await _run_disposers(holder)

                self._effects.append(dispose_with_task)
                return dispose_with_task
            # 同步模式：立即收集异步效果，保证后续卸载时序正确。
            drive_sync(consume_awaitable(), f"fiber <{self.name}> 的异步效果收集")
            self._effects.append(dispose_async)
            return dispose_async

        if isinstance(result, AsyncIterable):
            holder = []

            async def consume_async_iter() -> None:
                async for item in result:
                    if not callable(item):
                        raise InvalidEffect(item)
                    holder.append(item)

            async def dispose_async_iter() -> None:
                await _run_disposers(holder)

            if has_running_loop():
                task = asyncio.create_task(consume_async_iter())

                async def dispose_with_task_iter() -> None:
                    await task
                    await _run_disposers(holder)

                self._effects.append(dispose_with_task_iter)
                return dispose_with_task_iter
            # 同步模式：立即收集异步可迭代效果。
            drive_sync(consume_async_iter(), f"fiber <{self.name}> 的异步效果迭代")
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
        config = resolve_plugin_config(self.plugin, self.config)
        plugin = self.plugin
        if plugin is None:
            return None
        if inspect.isclass(plugin):
            from .service import Service

            if issubclass(plugin, Service):
                return plugin(self.ctx)
            return plugin(self.ctx, config)
        if callable(plugin):
            return await await_maybe(plugin(self.ctx, config))
        apply = getattr(plugin, "apply", None)
        if callable(apply):
            return await await_maybe(apply(self.ctx, config))
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
        reasons = {}
        for name in self.inject:
            reason = self.ctx._requirement_reason(name, self.requirements.get(name, []))
            if reason is not None:
                reasons[name] = reason
        self._unsatisfied = reasons
        if reasons:
            return None
        return tuple(sorted(self.inject))

    @property
    def unsatisfied(self) -> dict[str, str]:
        """最近一次依赖评估中不满足的服务及其原因（软等待诊断）。"""
        return dict(self._unsatisfied)

    def _schedule(self, coro: Awaitable[None]) -> None:
        """双模式调度：有事件循环时后台执行，否则内联同步驱动。"""
        if has_running_loop():
            task = asyncio.create_task(coro)
            self._inertia = task

            def _done(t: asyncio.Task[None]) -> None:
                if self._inertia is t:
                    self._inertia = None

            task.add_done_callback(_done)
            return
        # 同步模式：内联驱动，调用方返回时加载/卸载已完成。
        drive_sync(coro, f"fiber <{self.name}> 的生命周期转换")

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
            if self.ctx._requirement_reason(name, self.requirements.get(name, [])) is None
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
        except Exception as exc:  # noqa: BLE001
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

    @property
    def error(self) -> Exception | None:
        """最近的加载错误（如有）。"""
        return self._error

    def _assert_sync_context(self, action: str) -> None:
        if has_running_loop():
            raise AsyncRequiredError(f"fiber <{self.name}> 的{action}")

    def dispose_sync(self) -> None:
        """同步模式卸载：立即清理此 fiber、其子 fiber 与全部效果。

        只在没有运行事件循环的同步调用链中使用；若 fiber 仍在异步加载中，
        或清理过程需要事件循环服务，会抛出 :class:`AsyncRequiredError`。
        """
        if self.is_root:
            self.ctx.dispose_all_sync()
            return
        if self._disposed:
            return
        self._assert_sync_context("卸载")
        if self._inertia is not None:
            raise AsyncRequiredError(f"fiber <{self.name}> 仍在异步加载中")
        self._disposed = True
        self.target = None
        children = [
            child
            for child in list(self.ctx.root._fibers)
            if child.parent_fiber is self
        ]
        for child in children:
            child.dispose_sync()
        drive_sync(self._unload(), f"fiber <{self.name}> 的卸载")
        self.ctx.root._remove_fiber(self)

    def restart_sync(self) -> None:
        """同步模式重启：清理旧效果与旧服务后立即重新加载。"""
        self.assert_active()
        self._assert_sync_context("重启")
        if self._inertia is not None:
            raise AsyncRequiredError(f"fiber <{self.name}> 仍在异步加载中")
        self.target = None
        drive_sync(self._unload(), f"fiber <{self.name}> 的重启")
        self.refresh()
        self.check()

    def update_sync(self, config: Any) -> None:
        """同步模式应用新配置并重启 fiber。"""
        self.assert_active()
        self._assert_sync_context("配置更新")
        if self._inertia is not None:
            raise AsyncRequiredError(f"fiber <{self.name}> 仍在异步加载中")
        self.config = config
        self.target = None
        drive_sync(self._unload(), f"fiber <{self.name}> 的配置更新")
        self.refresh()
        self.check()

    def check(self) -> None:
        """同步检查加载错误：存在失败时（规范化后）立即抛出。"""
        if self._error is not None:
            normalize_sync_error(self._error, f"fiber <{self.name}> 的加载")

    async def restart(self) -> None:
        """清理当前效果；若依赖仍满足则重新加载。"""
        self.assert_active()
        if self._inertia is not None:
            await self._inertia
        self.target = None
        await self._unload()
        self.refresh()
        await self.wait()

    async def update(self, config: Any) -> None:
        """应用新配置并重启 fiber。"""
        self.assert_active()
        if self._inertia is not None:
            await self._inertia
        self.config = config
        self.target = None
        await self._unload()
        self.refresh()
        await self.wait()


__all__ = ["Fiber", "FiberState"]
