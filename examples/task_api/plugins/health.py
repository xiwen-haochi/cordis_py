"""健康检查插件。"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from cordis_py import Context, inject


@inject(["routes", "tenants"])
def plugin(ctx: Context, config: dict[str, Any]) -> None:
    routes = ctx.routes
    registry = ctx.tenants

    async def health(request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "tenants": registry.tenants(),
            }
        )

    undo = routes.add("GET", "/api/health", health, name="health.check")
    ctx.effect(lambda: undo)
