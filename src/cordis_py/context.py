"""Context: the first-class container and plugin entry point."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .errors import InactiveAccess, ServiceConflict, UndeclaredAccess
from .fiber import Fiber, FiberState
from .service import Service
from .utils import Disposable, Effect, Inject, await_maybe, resolve_inject

if TYPE_CHECKING:
    pass


@dataclass
class ServiceEntry:
    name: str
    value: Any
    provider: Fiber


@dataclass
class Listener:
    handler: Callable[..., Any]
    owner: Fiber
    once: bool = False


class Context:
    """Root or child dependency container.

    ``Context()`` creates the root. Plugins receive a derived child context,
    whose :attr:`fiber` is the plugin's runtime instance.
    """

    def __init__(self, parent: Context | None = None, fiber: Fiber | None = None) -> None:
        self._parent = parent
        self._fiber = fiber
        self._isolation: dict[str, Any] = {}
        self._intercept: dict[str, Any] = {}
        if parent is None:
            self._root = self
            self._services: dict[str, ServiceEntry] = {}
            self._listeners: dict[str, list[Listener]] = {}
            self._fibers: list[Fiber] = []
            self._uid = 0
            self._root_fiber = Fiber(self, None, None, {}, uid=0, is_root=True)
        else:
            self._root = parent._root

    # ------------------------------------------------------------------
    # basic properties
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
    def services(self) -> dict[str, Any]:
        return {
            name: entry.value
            for name, entry in self._root._services.items()
            if entry.provider.state == FiberState.ACTIVE
        }

    def _next_uid(self) -> int:
        self._root._uid += 1
        return self._root._uid

    # ------------------------------------------------------------------
    # service / coeffect primitives
    # ------------------------------------------------------------------

    def _has_active_service(self, name: str) -> bool:
        entry = self._root._services.get(name)
        return entry is not None and entry.provider.state == FiberState.ACTIVE

    def _get_active_service_value(self, name: str) -> Any:
        entry = self._root._services.get(name)
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
        for name in list(fiber.store):
            entry = self._root._services.get(name)
            if entry is not None and entry.provider is fiber:
                del self._root._services[name]
                fiber.store.discard(name)
                self._notify([name])

    def _remove_fiber(self, fiber: Fiber) -> None:
        if fiber in self._root._fibers:
            self._root._fibers.remove(fiber)
        # Cascade to child fibers, if any.
        for child in list(self._root._fibers):
            if getattr(child, "parent_fiber", None) is fiber:
                # This is intentionally fire-and-forget in the common case;
                # callers can await their own fiber's disposal.
                asyncio.create_task(child.dispose())

    def provide(self, name: str, value: Any) -> Disposable:
        """Register a service owned by the current fiber.

        The service becomes visible to dependents only while the owning fiber is
        ACTIVE. It is removed automatically when the fiber unloads.
        """
        fiber = self.fiber
        if fiber.is_root:
            # Root-level provide is allowed for boot-time services.
            pass
        entry = self._root._services.get(name)
        if entry is not None and entry.provider is not fiber:
            raise ServiceConflict(name, entry.provider.name)
        self._root._services[name] = ServiceEntry(name, value, fiber)
        fiber.store.add(name)
        if fiber.state == FiberState.ACTIVE:
            self._notify([name])

        def undo() -> None:
            current = self._root._services.get(name)
            if current is not None and current.provider is fiber:
                del self._root._services[name]
                fiber.store.discard(name)
                self._notify([name])

        return undo

    def get(self, name: str, strict: bool = True) -> Any:
        """Read a service value directly, without inject enforcement."""
        entry = self._root._services.get(name)
        if entry is None:
            return None
        if strict and entry.provider.state != FiberState.ACTIVE:
            return None
        return entry.value

    def set(self, name: str, value: Any) -> None:
        """Update a service value owned by the current fiber."""
        fiber = self.fiber
        entry = self._root._services.get(name)
        if entry is None:
            raise ServiceConflict(name, "nobody")
        if entry.provider is not fiber:
            raise ServiceConflict(name, entry.provider.name)
        entry.value = value
        if fiber.state == FiberState.ACTIVE:
            self._notify([name])

    # ------------------------------------------------------------------
    # effect
    # ------------------------------------------------------------------

    def effect(self, callback: Callable[[], Effect], label: str = "anonymous") -> Disposable:
        """Register a reversible effect on the current fiber."""
        return self.fiber.effect(callback, label)

    # ------------------------------------------------------------------
    # plugin loading
    # ------------------------------------------------------------------

    def _make_plugin_context(self, fiber: Fiber) -> Context:
        child = Context(self)
        child._fiber = fiber
        return child

    def plugin(self, plugin: Any, config: Any = None) -> Fiber:
        """Load a plugin in the current context and return its fiber."""
        if not callable(plugin) and not hasattr(plugin, "apply"):
            raise TypeError(f"invalid plugin: {plugin!r}")
        inject = resolve_inject(plugin)
        fiber = Fiber(
            self,
            plugin,
            config,
            inject,
            uid=self._next_uid(),
        )
        fiber.ctx = self._make_plugin_context(fiber)
        fiber.parent_fiber = None if self.fiber.is_root else self.fiber
        self._root._fibers.append(fiber)
        fiber.refresh()
        return fiber

    def inject(self, deps: Inject, callback: Callable[..., Any], config: Any = None) -> Fiber:
        """Run a callback once the requested services are available."""

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
    # events
    # ------------------------------------------------------------------

    def on(self, event: str, handler: Callable[..., Any], *, prepend: bool = False) -> Disposable:
        """Register an event listener owned by the current fiber."""
        fiber = self.fiber
        listener = Listener(handler, fiber)
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

    def once(self, event: str, handler: Callable[..., Any], *, prepend: bool = False) -> Disposable:
        """Register a one-shot event listener."""

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            undo()
            return handler(*args, **kwargs)

        return self.on(event, wrapper, prepend=prepend)

    def _dispatch(self, event: str) -> list[Listener]:
        listeners = list(self._root._listeners.get(event, []))
        for listener in listeners:
            if listener.once:
                if listener in self._root._listeners.get(event, []):
                    self._root._listeners[event].remove(listener)
        return listeners

    def emit(self, event: str, *args: Any) -> None:
        """Dispatch synchronously; async listeners are scheduled as tasks."""
        for listener in self._dispatch(event):
            try:
                result = listener.handler(*args)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception:
                # Event listener failures should not break other listeners.
                continue

    async def parallel(self, event: str, *args: Any) -> None:
        """Run all listeners concurrently and wait for completion."""
        listeners = self._dispatch(event)
        results = await asyncio.gather(
            *(listener.handler(*args) for listener in listeners),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise errors[0]

    async def serial(self, event: str, *args: Any) -> Any:
        """Run listeners in order, awaiting each, until a bail value is returned."""
        for listener in self._dispatch(event):
            result = await await_maybe(listener.handler(*args))
            if result is not None and result is not False:
                return result
        return None

    def bail(self, event: str, *args: Any) -> Any:
        """Run listeners synchronously until one returns a bail value."""
        for listener in self._dispatch(event):
            result = listener.handler(*args)
            if result is not None and result is not False:
                return result
        return None

    async def waterfall(self, event: str, *args: Any) -> Any:
        """Compose listeners as a middleware chain.

        Each listener receives ``(*args, next)``. Not calling ``next`` vetoes
        the remaining chain.
        """
        listeners = self._dispatch(event)

        async def run(index: int, values: tuple[Any, ...]) -> Any:
            if index >= len(listeners):
                return None
            listener = listeners[index]

            def next_fn(*next_args: Any) -> Any:
                return run(index + 1, next_args)

            result = listener.handler(*values, next_fn)
            return await await_maybe(result)

        return await run(0, args)

    # ------------------------------------------------------------------
    # scoped contexts
    # ------------------------------------------------------------------

    def isolate(self, name: str, realm: Any = None) -> Context:
        """Create a child context with an isolated service scope for *name*.

        This is a lightweight isolation layer: the child carries its own realm
        annotation. A full per-realm store will be layered on top in the loader.
        """
        child = Context(self)
        child._fiber = self.fiber
        child._isolation[name] = realm if realm is not None else object()
        return child

    def intercept(self, name: str, metadata: Any) -> Context:
        """Create a child context carrying intercept metadata for *name*."""
        child = Context(self)
        child._fiber = self.fiber
        child._intercept[name] = metadata
        return child

    # ------------------------------------------------------------------
    # attribute access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        fiber = self.fiber
        # A plugin's own committed view has highest priority.
        if name in fiber.committed:
            return fiber.committed[name]
        if name in fiber.inject:
            raise InactiveAccess(name)
        if fiber.is_root:
            # Direct root-context access is allowed for boot-time services.
            value = self._get_active_service_value(name)
            if value is not None:
                return value
            raise UndeclaredAccess(name)
        # Walk up the context tree.
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
        """Dispose every plugin fiber in reverse load order."""
        for fiber in reversed(list(self._root._fibers)):
            await fiber.dispose()

    def __repr__(self) -> str:
        return f"Context <{self.fiber.name}>"


__all__ = ["Context", "ServiceEntry", "Listener"]
