"""Shared utilities for the Cordis Python runtime."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Mapping
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
    """Return whether *obj* is an async function or an object with async __call__."""
    if inspect.iscoroutinefunction(obj):
        return True
    call = getattr(obj, "__call__", None)
    return inspect.iscoroutinefunction(call)


def maybe_await(value: Any) -> Any:
    """Await *value* if it is awaitable, otherwise return it as-is."""
    if inspect.isawaitable(value):
        return value
    return value


async def await_maybe(value: Any) -> Any:
    """Await *value* if it is awaitable, otherwise return it."""
    if inspect.isawaitable(value):
        return await value
    return value


def resolve_inject(plugin: Any) -> dict[str, Any]:
    """Normalize a plugin's ``inject`` metadata to a name -> config map."""
    raw = getattr(plugin, "inject", None)
    if raw is None:
        return {}
    if isinstance(raw, list):
        return {name: None for name in raw}
    if isinstance(raw, Mapping):
        return dict(raw)
    raise TypeError(f"invalid inject declaration: {raw!r}")


def collect_disposers(effect: Effect) -> list[Disposable]:
    """Collect disposers from supported effect return shapes (sync only).

    Async iterables are handled by the fiber/context callers because they need
    an async loop.
    """
    if effect is None:
        return []
    if callable(effect):
        return [effect]
    if isinstance(effect, Iterable) and not isinstance(effect, (str, bytes)):
        return [item for item in effect if callable(item)]
    return []
