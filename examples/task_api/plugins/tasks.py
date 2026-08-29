"""业务插件：任务 CRUD。

在装配顺序中位于 http / tenant 之前：因 ``routes`` / ``registry`` 服务缺失而
软等待（响应式依赖），提供者出现后自动激活并注册路由。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from cordis_py import Context, inject, require

from .tenant import TenantRegistry


@inject(["routes", "tenants"])
@require("tenants", ">=1.0")
def plugin(ctx: Context, config: dict[str, Any]) -> None:
    routes = ctx.routes
    registry: TenantRegistry = ctx.tenants
    logger = ctx.get("log")

    def bump(name: str) -> None:
        metrics = ctx.get("metrics")
        if metrics is not None:
            metrics.bump(name)

    # 每次请求经服务解析当前 registry：配置热更/源码热替换后拿到最新实现
    # （插件激活时固化的引用会滞后于热更，见 store_for 注释）。
    def store_for(request: Request) -> Any:
        current = ctx.get("tenants")
        store = (current or registry).store(request.state.tenant)
        return store

    async def list_tasks(request: Request) -> Response:
        tasks = store_for(request).list()
        page_size = int(config.get("page_size", 20))
        tasks = tasks[:page_size]
        bump("tasks.list")
        return JSONResponse({"items": tasks, "count": len(tasks)})

    async def create_task(request: Request) -> Response:
        payload = await request.json()
        title = str(payload.get("title") or "").strip()
        if not title:
            return JSONResponse({"error": "title_required"}, status_code=422)
        store = store_for(request)
        task = store.create(title, priority=int(payload.get("priority", 1)))
        ctx.emit("task/created", request.state.tenant, task)
        bump("tasks.created")
        return JSONResponse(task, status_code=201)

    async def get_task(request: Request, task_id: str) -> Response:
        task = store_for(request).get(task_id)
        if task is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse(task)

    async def delete_task(request: Request, task_id: str) -> Response:
        task = store_for(request).delete(task_id)
        if task is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        ctx.emit("task/deleted", request.state.tenant, task)
        bump("tasks.deleted")
        return JSONResponse({"deleted": task_id})

    # 注册路由并登记可逆 disposer（HMR 重载时整体替换）。
    undos = [
        routes.add("GET", "/api/tasks", list_tasks, name="tasks.list"),
        routes.add("POST", "/api/tasks", create_task, name="tasks.create"),
        routes.add("GET", "/api/tasks/{task_id}", get_task, name="tasks.get"),
        routes.add("DELETE", "/api/tasks/{task_id}", delete_task, name="tasks.delete"),
    ]

    def undo_all() -> None:
        for undo in undos:
            undo()

    ctx.effect(lambda: undo_all)
    if logger is not None:
        logger("tasks").info("任务 CRUD 插件已激活（响应式依赖装配）")
