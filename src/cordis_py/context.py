"""Context：第一类容器与插件入口。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .errors import (
    AsyncRequiredError,
    InactiveAccess,
    ServiceConflict,
    UndeclaredAccess,
)
from .fiber import Fiber, FiberState
from .utils import (
    Disposable,
    Effect,
    Inject,
    await_maybe,
    constraint_matches,
    has_running_loop,
    merge_config,
    normalize_sync_error,
    resolve_inject,
    resolve_requirements,
)


@dataclass
class ServiceEntry:
    name: str
    value: Any
    provider: Fiber
    version: str | None = None


@dataclass
class Listener:
    handler: Callable[..., Any]
    owner: Fiber
    ctx: Context | None = None
    once: bool = False
    global_: bool = False


class Context:
    """根或子依赖容器。

    ``Context()`` 创建根上下文。插件会获得一个派生的子上下文，
    其 :attr:`fiber` 即插件运行实例。
    """

    def __init__(self, parent: Context | None = None, fiber: Fiber | None = None) -> None:
        self._parent = parent
        self._fiber = fiber
        self._isolation: dict[str, Any] = {}
        self._intercept: dict[str, Any] = {}
        self._filter: Callable[[Context], bool] | None = None
        self._scope_key: Any = None
        if parent is None:
            self._root = self
            self._services: dict[str, ServiceEntry] = {}
            self._listeners: dict[str, list[Listener]] = {}
            self._fibers: list[Fiber] = []
            self._uid = 0
            self._root_fiber = Fiber(self, None, None, {}, uid=0, is_root=True)
        else:
            self._root = parent._root
            self._filter = parent._filter
            self._scope_key = parent._scope_key

    # ------------------------------------------------------------------
    # 基本属性
    # ------------------------------------------------------------------

    @property
    def root(self) -> Context:
        return self._root

    @property
    def fiber(self) -> Fiber:
        if self._fiber is not None:
            return self._fiber
        return self._root_fiber

    @property
    def parent(self) -> Context | None:
        return self._parent

    @property
    def events(self) -> Any:
        """绑定到当前上下文的事件服务外观。"""
        from .events import EventsService

        if getattr(self, "_events_cache", None) is None:
            self._events_cache = EventsService(self)
        return self._events_cache

    @property
    def registry(self) -> Any:
        """绑定到当前上下文的插件注册表外观。"""
        from .registry import RegistryService

        if getattr(self, "_registry_cache", None) is None:
            self._registry_cache = RegistryService(self)
        return self._registry_cache

    @property
    def services(self) -> dict[str, Any]:
        return {
            entry.name: entry.value
            for entry in self._root._services.values()
            if entry.provider.state == FiberState.ACTIVE
        }

    def _next_uid(self) -> int:
        self._root._uid += 1
        return self._root._uid

    # ------------------------------------------------------------------
    # 服务 / coeffect 原语
    # ------------------------------------------------------------------

    def _realm_for(self, name: str) -> Any:
        return self._isolation.get(name)

    def _service_key(self, name: str) -> tuple[Any, str]:
        return (self._realm_for(name), name)

    def _has_active_service(self, name: str) -> bool:
        entry = self._root._services.get(self._service_key(name))
        return entry is not None and entry.provider.state == FiberState.ACTIVE

    def _get_active_service_value(self, name: str) -> Any:
        entry = self._root._services.get(self._service_key(name))
        if entry is None or entry.provider.state != FiberState.ACTIVE:
            return None
        return entry.value

    def _notify(self, names: Iterable[str]) -> None:
        names = set(names)
        for fiber in list(self._root._fibers):
            if any(name in fiber.inject for name in names):
                fiber.refresh()

    def _notify_provided(self, names: Iterable[str]) -> None:
        names = [name for name in names if self._has_active_service(name)]
        if names:
            self._notify(names)

    def _remove_fiber_services(self, fiber: Fiber) -> None:
        for key, entry in list(self._root._services.items()):
            if entry.provider is fiber:
                del self._root._services[key]
                fiber.store.discard(entry.name)
                self._notify([entry.name])

    def _remove_fiber(self, fiber: Fiber) -> None:
        if fiber in self._root._fibers:
            self._root._fibers.remove(fiber)

    def _requirement_reason(self, name: str, constraints: list[Any]) -> str | None:
        """返回服务 *name* 的约束不满足原因；满足或无需约束时返回 ``None``。

        软等待语义：服务未提供与约束不满足都不构成错误，消费者保持 PENDING，
        提供方变化后由响应式机制重新评估。
        """
        entry = self._root._services.get(self._service_key(name))
        if entry is None or entry.provider.state != FiberState.ACTIVE:
            return "服务未提供"
        for constraint in constraints:
            ok, reason = constraint_matches(constraint, entry.value, entry.version)
            if not ok:
                return reason
        return None

    def provide(self, name: str, value: Any, *, version: str | None = None) -> Disposable:
        """注册由当前 fiber 拥有的服务。

        只有属主 fiber 处于 ACTIVE 状态时，该服务才对依赖方可见；
        fiber 卸载时会自动移除该服务。*version* 用于消费方的版本约束校验
        （见 :func:`require`）。
        """
        fiber = self.fiber
        key = self._service_key(name)
        entry = self._root._services.get(key)
        if entry is not None and entry.provider is not fiber:
            raise ServiceConflict(name, entry.provider.name)
        self._root._services[key] = ServiceEntry(name, value, fiber, version)
        fiber.store.add(name)
        if fiber.state == FiberState.ACTIVE:
            self._notify([name])

        def undo() -> None:
            current = self._root._services.get(key)
            if current is not None and current.provider is fiber:
                del self._root._services[key]
                fiber.store.discard(name)
                self._notify([name])

        return undo

    def get(self, name: str, strict: bool = True) -> Any:
        """直接读取服务值，不执行 inject 强制校验。"""
        entry = self._root._services.get(self._service_key(name))
        if entry is None:
            return None
        if strict and entry.provider.state != FiberState.ACTIVE:
            return None
        return entry.value

    def set(self, name: str, value: Any) -> None:
        """更新当前 fiber 拥有的服务值。"""
        fiber = self.fiber
        key = self._service_key(name)
        entry = self._root._services.get(key)
        if entry is None:
            raise ServiceConflict(name, "nobody")
        if entry.provider is not fiber:
            raise ServiceConflict(name, entry.provider.name)
        entry.value = value
        if fiber.state == FiberState.ACTIVE:
            self._notify([name])

    # ------------------------------------------------------------------
    # 效果
    # ------------------------------------------------------------------

    def effect(self, callback: Callable[[], Effect], label: str = "anonymous") -> Disposable:
        """在当前 fiber 上注册可逆效果。"""
        return self.fiber.effect(callback, label)

    # ------------------------------------------------------------------
    # 插件加载
    # ------------------------------------------------------------------

    def _make_plugin_context(self, fiber: Fiber) -> Context:
        child = Context(self)
        child._fiber = fiber
        child._isolation = dict(self._isolation)
        # intercept 采用逐节点存储，合并时沿 _parent 链回溯（见 intercept_config）。
        return child

    def plugin(self, plugin: Any, config: Any = None) -> Fiber:
        """在当前上下文中加载插件并返回其 fiber。"""
        if not callable(plugin) and not hasattr(plugin, "apply"):
            raise TypeError(f"invalid plugin: {plugin!r}")
        inject = resolve_inject(plugin)
        requirements = resolve_requirements(plugin, inject)
        fiber = Fiber(
            self,
            plugin,
            config,
            inject,
            requirements,
            uid=self._next_uid(),
        )
        fiber.ctx = self._make_plugin_context(fiber)
        # 插件在 inject 中声明的非空配置进入其上下文的拦截链（与 Node 版一致）：
        # 该插件提供的服务可以经由 resolve_config 读取合并后的配置。
        fiber.ctx._intercept = {name: cfg for name, cfg in inject.items() if cfg is not None}
        fiber.parent_fiber = None if self.fiber.is_root else self.fiber
        self._root._fibers.append(fiber)
        fiber.refresh()
        # 同步模式：加载内联完成，失败在此处立即向调用方抛出。
        if not has_running_loop() and fiber.error is not None:
            normalize_sync_error(fiber.error, f"插件 {fiber.name} 的加载")
        return fiber

    def inject(self, deps: Inject, callback: Callable[..., Any], config: Any = None) -> Fiber:
        """当所需服务可用时运行回调。"""

        def wrapper(ctx: Context, conf: Any) -> Any:
            return callback(ctx, conf)

        if isinstance(deps, list):
            wrapper.inject = deps  # type: ignore[attr-defined]
        elif isinstance(deps, dict):
            wrapper.inject = dict(deps)  # type: ignore[attr-defined]
        else:
            wrapper.inject = []  # type: ignore[attr-defined]
        return self.plugin(wrapper, config)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def on(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        prepend: bool = False,
        global_: bool = False,
    ) -> Disposable:
        """注册由当前 fiber 拥有的事件监听器。

        *global_* 为 True 时该监听器无视任何派发过滤（对应 Node
        ``EventOptions.global``：无论上下文过滤器如何都接收事件）。
        """
        fiber = self.fiber
        listener = Listener(handler, fiber, ctx=self, global_=global_)
        listeners = self._root._listeners.setdefault(event, [])
        if prepend:
            listeners.insert(0, listener)
        else:
            listeners.append(listener)

        def undo() -> None:
            if listener in listeners:
                listeners.remove(listener)

        fiber.add_effect(undo)
        return undo

    def once(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        prepend: bool = False,
        global_: bool = False,
    ) -> Disposable:
        """注册一次性事件监听器。"""
        fiber = self.fiber
        listeners = self._root._listeners.setdefault(event, [])

        def undo() -> None:
            if listener in listeners:
                listeners.remove(listener)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            undo()
            return handler(*args, **kwargs)

        listener = Listener(wrapper, fiber, ctx=self, global_=global_)
        if prepend:
            listeners.insert(0, listener)
        else:
            listeners.append(listener)
        fiber.add_effect(undo)
        return undo

    @staticmethod
    def _receiver_filter(receiver: Any) -> Callable[[Context], bool] | None:
        """从派发接收者对象上提取过滤器。

        receiver 为 :class:`Context` → 用其 ``_filter``（经 :meth:`filtered` 设置）；
        任意对象携带 ``context_filter`` 属性 → 用该谓词；否则视为不过滤。
        """
        if isinstance(receiver, Context):
            return receiver._filter
        return getattr(receiver, "context_filter", None)

    def _dispatch(self, event: str, receiver: Any | None = None) -> list[Listener]:
        """收集监听器并按接收者过滤器筛选。

        与 Node ``EventsService.dispatch`` 对齐：只有 ``global_`` 监听器、未设置
        过滤器、或谓词对监听器注册上下文返回真值的监听器参与本次派发。
        """
        listeners = list(self._root._listeners.get(event, []))
        if receiver is not None:
            predicate = self._receiver_filter(receiver)
            if predicate is not None:
                listeners = [
                    listener
                    for listener in listeners
                    if listener.global_ or predicate(listener.ctx or listener.owner.ctx)
                ]
        live = self._root._listeners.get(event, [])
        for listener in listeners:
            if listener.once and listener in live:
                live.remove(listener)
        return listeners

    def emit(self, event: str, *args: Any, receiver: Any | None = None) -> None:
        """同步分发事件。

        有运行事件循环时，异步监听器会被调度为后台任务；
        无事件循环（同步模式）时只能执行同步监听器，若遇到异步监听器会抛出
        :class:`AsyncRequiredError`，避免监听器代码被静默丢弃。

        *receiver* 为派发接收者（:class:`Context` 或携带 ``context_filter`` 的载体），
        其过滤器决定哪些监听器对本派发可见。
        """
        for listener in self._dispatch(event, receiver):
            try:
                result = listener.handler(*args)
            except Exception:  # noqa: BLE001, S112
                # 单个监听器失败不应中断其他监听器。
                continue
            if not inspect.isawaitable(result):
                continue
            if has_running_loop():
                asyncio.create_task(result)
            else:
                result.close()
                raise AsyncRequiredError(f"事件 {event!r} 的异步监听器")

    async def parallel(self, event: str, *args: Any, receiver: Any | None = None) -> None:
        """并发运行所有监听器并等待完成（同步监听器同样受支持）。"""
        listeners = self._dispatch(event, receiver)
        results = await asyncio.gather(
            *(await_maybe(listener.handler(*args)) for listener in listeners),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise errors[0]

    async def serial(self, event: str, *args: Any, receiver: Any | None = None) -> Any:
        """按顺序运行监听器并逐个 await，直到返回短路值。"""
        for listener in self._dispatch(event, receiver):
            result = await await_maybe(listener.handler(*args))
            if result is not None and result is not False:
                return result
        return None

    def bail(self, event: str, *args: Any, receiver: Any | None = None) -> Any:
        """同步运行监听器，直到其中一个返回短路值。

        同步模式下无法执行异步监听器，遇到时抛出 :class:`AsyncRequiredError`。
        """
        for listener in self._dispatch(event, receiver):
            result = listener.handler(*args)
            if inspect.isawaitable(result):
                result.close()
                raise AsyncRequiredError(f"事件 {event!r} 的异步监听器")
            if result is not None and result is not False:
                return result
        return None

    async def waterfall(self, event: str, *args: Any, receiver: Any | None = None) -> Any:
        """将监听器组合成中间件链。

        每个监听器接收 ``(*args, next)``；不调用 ``next`` 会否决后续链路，
        ``next()`` 无参时沿用当前参数。
        """
        return await self._run_waterfall(self._dispatch(event, receiver), args)

    async def _run_waterfall(self, listeners: list[Listener], args: tuple[Any, ...], fallback: Callable[..., Any] | None = None) -> Any:
        """按注册顺序运行监听器中间件链；不调用 ``next`` 即短路。

        *fallback* 在链尾 ``next()`` 时被调用（用于 internal/config 返回原始配置）。
        """
        async def run(index: int, values: tuple[Any, ...]) -> Any:
            if index >= len(listeners):
                if fallback is None:
                    return None
                return await await_maybe(fallback(*values))
            listener = listeners[index]

            def next_fn(*next_args: Any) -> Any:
                # 无参调用 next() 时沿用当前参数，与 Node 中间件语义一致。
                return run(index + 1, next_args if next_args else values)

            result = listener.handler(*values, next_fn)
            return await await_maybe(result)

        return await run(0, args)

    # ------------------------------------------------------------------
    # 配置 overlay
    # ------------------------------------------------------------------

    def _config_listeners_for(self, fiber: Fiber) -> list[Listener]:
        """收集适用于目标 fiber 的 ``internal/config`` 监听器。

        只包含注册者严格祖先（沿 ``parent_fiber`` 链）注册的监听器；
        root 上注册的监听器对全体生效。兄弟分支互不干扰。
        """
        listeners = self._root._listeners.get("internal/config", [])

        def applies(listener: Listener) -> bool:
            owner = listener.owner
            if owner.is_root:
                return True
            node = fiber.parent_fiber
            while node is not None:
                if node is owner:
                    return True
                node = node.parent_fiber
            return False

        return [listener for listener in listeners if applies(listener)]

    async def _resolve_config_overlay(self, fiber: Fiber, config: Any) -> Any:
        """应用 ``internal/config`` 瀑布链改写插件配置。

        任何插件（含 ``update()`` 重载）激活前都经过这里：先 overlay 改写，
        再进入 ``Config`` 校验（与 Node 版 Cordis 的顺序一致）。
        """
        listeners = self._config_listeners_for(fiber)
        if not listeners:
            return config
        # 链尾 next() 返回原始配置文件（与 Node 的 waterfall(config, () => config) 一致）。
        return await self._run_waterfall(
            listeners,
            (fiber, config),
            fallback=lambda fiber, config: config,
        )

    # ------------------------------------------------------------------
    # 作用域上下文
    # ------------------------------------------------------------------

    def isolate(self, name: str, realm: Any = None) -> Context:
        """为 *name* 创建隔离服务作用域的子上下文。

        这是轻量隔离层：子上下文携带自己的 realm 标记。
        更完整的按 realm 存储后续会在 Loader 中叠加。
        """
        child = Context(self)
        child._fiber = self.fiber
        child._isolation = dict(self._isolation)
        child._isolation[name] = realm if realm is not None else object()
        return child

    def filtered(self, predicate: Callable[[Context], bool]) -> Context:
        """创建携带监听器过滤谓词（filter）的子上下文。

        该子上下文（及其后代）作为派发接收者时，只有谓词对监听器注册上下文
        返回真值的监听器（或 ``global_`` 监听器）会被触发——对应 Node 版 Cordis
        的 ``Context.filter``（经 ``extend`` 设置）：过滤边界由“谁发起接收者
        派发”决定，是构建不可信插件隔离（配合 :mod:`~cordis_py.scope` 路由）的
        核心原语。
        """
        child = Context(self)
        child._fiber = self.fiber
        child._isolation = dict(self._isolation)
        child._filter = predicate
        return child

    def intercept(self, name: str, metadata: Any) -> Context:
        """创建携带 *name* 拦截配置的子上下文。

        拦截配置只影响服务配置的解析（见 :meth:`intercept_config`），不改变
        服务查找与依赖响应：该服务在注入语义上仍然与父作用域一致。
        祖先条目先应用，越靠近当前上下文的条目优先级越高。
        """
        child = Context(self)
        child._fiber = self.fiber
        child._isolation = dict(self._isolation)
        child._intercept = {name: metadata}
        return child

    def intercept_config(self, name: str) -> Any:
        """返回沿祖先链为 *name* 合并后的拦截配置。

        从根向叶子合并：祖先条目先应用，越靠近当前上下文的条目优先级越高
        （与 Node 版 Cordis 的 ``resolveConfig`` 语义一致）。条目为映射时浅合并，
        非映射条目整体替换；没有任何条目时返回 ``None``。
        """
        entries: list[Any] = []
        node: Context | None = self
        while node is not None:
            if name in node._intercept:
                entries.append(node._intercept[name])
            node = node._parent
        merged: Any = None
        for entry in reversed(entries):
            merged = merge_config(merged, entry)
        return merged

    # ------------------------------------------------------------------
    # 属性访问
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        fiber = self.fiber
        # 插件自身的 committed 视图具有最高优先级。
        if name in fiber.committed:
            return fiber.committed[name]
        if name in fiber.inject:
            raise InactiveAccess(name)
        if fiber.is_root:
            # 根上下文的直接访问允许用于启动期服务。
            value = self._get_active_service_value(name)
            if value is not None:
                return value
            raise UndeclaredAccess(name)
        # 沿上下文树向上查找。
        current = self._parent
        while current is not None:
            current_fiber = current.fiber
            if name in current_fiber.committed:
                return current_fiber.committed[name]
            if name in current_fiber.inject:
                raise InactiveAccess(name)
            if current_fiber.is_root:
                raise UndeclaredAccess(name)
            current = current._parent
        raise UndeclaredAccess(name)

    async def dispose_all(self) -> None:
        """按加载顺序的逆序卸载所有插件 fiber。"""
        for fiber in reversed(list(self._root._fibers)):
            await fiber.dispose()

    def dispose_all_sync(self) -> None:
        """按加载顺序的逆序同步卸载所有插件 fiber。

        仅在没有运行事件循环的同步调用链中使用；若任一 fiber 仍在异步加载中，
        会抛出 :class:`AsyncRequiredError`。
        """
        for fiber in reversed(list(self._root._fibers)):
            fiber.dispose_sync()

    def __repr__(self) -> str:
        return f"Context <{self.fiber.name}>"


__all__ = ["Context", "Listener", "ServiceEntry"]
