"""标签统计插件：tags 服务的第一位消费者（演示版本契约 @require）。

契约链（与 tenant → tasks 同款模式）：

- **提供方**：``tags`` 插件 ``ctx.provide("tags", service, version="1.0")``；
- **消费方**：本插件 ``@inject(["routes", "tenants", "tags"])`` +
  ``@require("tags", ">=1.0")`` —— 契约满足才激活，不满足保持 PENDING（软等待）；
- 接口「新增能力 = 新增插件」：``GET /api/tags/stats`` 全租户标签统计，
  消费 ``ctx.tenants`` / ``ctx.tags`` 两个服务（都不认识具体实现类）。

装饰器本身来自 ``cordis_py``（``from cordis_py import inject, require``），
在插件定义处声明即可，无需改任何核心代码。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from cordis_py import Context, inject, require


@inject(["routes", "tenants", "tags"])
@require("tags", ">=1.0")  # PEP 440 版本契约：tags 服务须 >= 1.0
@require("tags", lambda svc: hasattr(svc, "list"))  # 接口谓词：能 list 才算契约满足
def plugin(ctx: Context, config: dict[str, Any]) -> None:
    routes = ctx.routes
    registry = ctx.tenants
    tags_svc = ctx.tags

    async def stats(request: Request) -> Response:
        rows = []
        for tenant in registry.tenants():
            store = registry.store(tenant)
            for task in store.list():
                task_id = task["id"]
                rows.append(
                    {"tenant": tenant, "task": task_id, "tags": tags_svc.list(task_id)}
                )
        total = sum(len(row["tags"]) for row in rows)
        return JSONResponse({"total": total, "tasks": rows})

    undo = routes.add("GET", "/api/tags/stats", stats, name="tags.stats")
    ctx.effect(lambda: undo)
