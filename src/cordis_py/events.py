"""兼容 Cordis API 的事件服务外观。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from .context import Context
from .utils import Disposable

DispatchMode = Literal["emit", "parallel", "serial", "bail", "waterfall"]


class EventsService:
    """将事件操作委托给 :class:`Context`。"""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def on(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        prepend: bool = False,
        global_: bool = False,
    ) -> Disposable:
        return self.ctx.on(event, handler, prepend=prepend, global_=global_)

    def once(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        prepend: bool = False,
        global_: bool = False,
    ) -> Disposable:
        return self.ctx.once(event, handler, prepend=prepend, global_=global_)

    def emit(self, event: str, *args: Any, receiver: Any | None = None) -> None:
        self.ctx.emit(event, *args, receiver=receiver)

    async def parallel(self, event: str, *args: Any, receiver: Any | None = None) -> None:
        await self.ctx.parallel(event, *args, receiver=receiver)

    async def serial(self, event: str, *args: Any, receiver: Any | None = None) -> Any:
        return await self.ctx.serial(event, *args, receiver=receiver)

    def bail(self, event: str, *args: Any, receiver: Any | None = None) -> Any:
        return self.ctx.bail(event, *args, receiver=receiver)

    async def waterfall(self, event: str, *args: Any, receiver: Any | None = None) -> Any:
        return await self.ctx.waterfall(event, *args, receiver=receiver)


__all__ = ["DispatchMode", "EventsService"]
