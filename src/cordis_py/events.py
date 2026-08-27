"""Event service facade compatible with the Cordis API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from .context import Context
from .utils import Disposable

DispatchMode = Literal["emit", "parallel", "serial", "bail", "waterfall"]


class EventsService:
    """Delegate event operations to a :class:`Context`."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def on(self, event: str, handler: Callable[..., Any], *, prepend: bool = False) -> Disposable:
        return self.ctx.on(event, handler, prepend=prepend)

    def once(self, event: str, handler: Callable[..., Any], *, prepend: bool = False) -> Disposable:
        return self.ctx.once(event, handler, prepend=prepend)

    def emit(self, event: str, *args: Any) -> None:
        self.ctx.emit(event, *args)

    async def parallel(self, event: str, *args: Any) -> None:
        await self.ctx.parallel(event, *args)

    async def serial(self, event: str, *args: Any) -> Any:
        return await self.ctx.serial(event, *args)

    def bail(self, event: str, *args: Any) -> Any:
        return self.ctx.bail(event, *args)

    async def waterfall(self, event: str, *args: Any) -> Any:
        return await self.ctx.waterfall(event, *args)


__all__ = ["EventsService", "DispatchMode"]
