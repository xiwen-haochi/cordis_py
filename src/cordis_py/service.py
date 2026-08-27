"""Service base class and dependency-injection decorator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar, overload

from .utils import resolve_inject

T = TypeVar("T")


class Service:
    """Base class for services registered on a Cordis context.

    Subclasses call ``super().__init__(ctx, name)`` or rely on the class-level
    ``provide`` attribute for the service name.
    """

    provide: str | None = None

    def __init__(self, ctx: Any, name: str | None = None) -> None:
        self.ctx = ctx
        self.name = name or self.provide or type(self).__name__.lower()
        ctx.provide(self.name, self)


def _attach_inject(target: Any, deps: list[str] | Mapping[str, Any]) -> Any:
    old = getattr(target, "inject", None)
    if old is None:
        combined: dict[str, Any] = {}
    elif isinstance(old, list):
        combined = {name: None for name in old}
    elif isinstance(old, Mapping):
        combined = dict(old)
    else:
        combined = {}

    if isinstance(deps, list):
        for name in deps:
            combined.setdefault(name, None)
    else:
        for name, config in deps.items():
            combined.setdefault(name, config)
    target.inject = combined  # type: ignore[attr-defined]
    return target


@overload
def inject(name: str, /) -> Callable[[T], T]: ...
@overload
def inject(names: list[str], /) -> Callable[[T], T]: ...
@overload
def inject(deps: Mapping[str, Any], /) -> Callable[[T], T]: ...


def inject(deps: str | list[str] | Mapping[str, Any], /):
    """Declare required services on a plugin function or class.

    Usage::

        @inject("model")
        def my_plugin(ctx, config): ...

        @inject(["model", "storage"])
        class MyPlugin: ...

        @inject({"model": {"timeout": 10}})
        def another(ctx, config): ...
    """
    if isinstance(deps, str):
        dep_list = [deps]
    elif isinstance(deps, list):
        dep_list = deps
    elif isinstance(deps, Mapping):
        dep_map = dict(deps)

        def decorator(target: T) -> T:
            return _attach_inject(target, dep_map)  # type: ignore[return-value]

        return decorator
    else:
        raise TypeError("inject() expects a service name, a list of names, or a mapping")

    def decorator(target: T) -> T:
        return _attach_inject(target, dep_list)  # type: ignore[return-value]

    return decorator


__all__ = ["Service", "inject"]
