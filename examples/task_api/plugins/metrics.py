"""指标插件：计数服务与 /api/metrics 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from cordis_py import Context, inject


class Metrics:
    """命名计数器收集器。"""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self._counters: dict[str, int] = {}

    def bump(self, name: str, delta: int = 1) -> None:
        key = f"{self.namespace}.{name}"
        self._counters[key] = self._counters.get(key, 0) + delta

    def snapshot(self) -> dict[str, int]:
        return dict(self._counters)


@inject(["routes"])
def plugin(ctx: Context, config: dict[str, Any]) -> None:
    ns = str(config.get("namespace", "cordis"))
    metrics = Metrics(ns)
    routes = ctx.routes

    async def dump(request: Request) -> Response:
        return JSONResponse(metrics.snapshot())

    # 路由注册可逆：fiber dispose 时自动卸载。
    undo = routes.add("GET", "/api/metrics", dump, name="metrics.dump")
    ctx.effect(lambda: undo)

    ctx.provide("metrics", metrics)
