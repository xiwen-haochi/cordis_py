"""Plugin registry convenience service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .context import Context
from .fiber import Fiber
from .utils import Inject


class RegistryService:
    """Thin wrapper exposing plugin registration on a context."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def plugin(self, plugin: Any, config: Any = None) -> Fiber:
        return self.ctx.plugin(plugin, config)

    def inject(self, deps: Inject, callback: Callable[..., Any], config: Any = None) -> Fiber:
        return self.ctx.inject(deps, callback, config)

    def has(self, plugin: Any) -> bool:
        return any(fiber.plugin is plugin for fiber in self.ctx.root._fibers)

    @property
    def size(self) -> int:
        return len(self.ctx.root._fibers)


__all__ = ["RegistryService"]
