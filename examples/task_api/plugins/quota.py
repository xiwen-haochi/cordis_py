"""限流插件：固定窗口限流，超标时在 http/request 瀑布链短路返回 429。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from cordis_py import Context


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    limit = int(config.get("limit", 5))
    window = float(config.get("window", 30))
    # 状态：租户 → [窗口起点, 已用次数]
    windows: dict[str, list[float]] = {}
    logger = ctx.get("log")

    def check(tenant: str | None, request: Request, next_: Callable[..., Any]) -> Any:
        # 公开路径（无租户绑定）的请求不限流（如健康检查轮询）。
        if tenant is None:
            return next_()
        now = time.monotonic()
        row = windows.setdefault(tenant or "<none>", [now, 0.0])
        if now - row[0] >= window:
            row[0], row[1] = now, 0.0
        row[1] += 1
        if row[1] > limit:
            if logger is not None:
                logger("quota").warning("rate limited tenant=%s", tenant)
            return JSONResponse(
                {"error": "rate_limited", "tenant": tenant}, status_code=429
            )
        return next_()

    # 瀑布链监听器：http 中间件每次请求都会运行本链。
    ctx.on("http/request", check)
