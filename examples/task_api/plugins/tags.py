"""标签插件：给任务打标签/查标签（演示"全新插件 + 新接口"的完整形态）。

新增的正交能力：把"标签"做成一个插件，而不是加进 tasks.py ——
- 接口：``POST /api/tasks/{task_id}/tags`` 打标签、``GET /api/tasks/{task_id}/tags`` 查标签；
- 与现有插件零耦合：只消费 ``routes`` / ``tenants`` 服务，不 import 任何业务代码；
- 数据为内存字典（与内存 TaskStore 同语义），可换成 SQLite/Redis 而接口不变；
- 路由经 ``routes.add()`` + ``ctx.effect`` 登记为可逆效果：HMR 重载/插件卸载时
  自动移除，不会出现"旧路由还在"。

任务的存在性经租户隔离作用域校验（``tenants.store(...).get(task_id)``）：
globex 的请求对 acme 的任务返回 404（realm 键空间物理隔离生效）。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from cordis_py import Context, inject


class TagsService:
    """标签服务契约：``add(task_id, tag)`` / ``list(task_id) -> list[str]``。

    以服务形式提供（带版本号），消费者可经 ``@require("tags", ">=1.0")``
    声明约束——换实现（如 SQLite 后端）只需换插件、契约不变。
    """

    def __init__(self) -> None:
        self._tags: dict[str, list[str]] = {}

    def add(self, task_id: str, tag: str) -> None:
        self._tags.setdefault(task_id, []).append(tag)

    def list(self, task_id: str) -> list[str]:
        return list(self._tags.get(task_id, []))


@inject(["routes", "tenants"])
def plugin(ctx: Context, config: dict[str, Any]) -> None:
    routes = ctx.routes
    tenants = ctx.tenants
    logger = ctx.get("log")
    # 标签数据：{task_id: [tag, ...]}（内存后端；契约与存储插件相同，可替换持久化）。
    service = TagsService()

    def task_exists(task_id: str, request: Request) -> bool:
        store = tenants.store(request.state.tenant)
        return store.get(task_id) is not None

    def not_found(task_id: str) -> JSONResponse:
        return JSONResponse({"error": "not_found", "task_id": task_id}, status_code=404)

    async def add_tag(request: Request, task_id: str) -> Response:
        if not task_exists(task_id, request):
            return not_found(task_id)
        payload = await request.json()
        tag = str(payload.get("tag") or "").strip()
        if not tag:
            return JSONResponse({"error": "tag_required"}, status_code=422)
        service.add(task_id, tag)
        if logger is not None:
            logger("tags").info("tagged task=%s tag=%s tenant=%s", task_id, tag, request.state.tenant)
        return JSONResponse({"task_id": task_id, "tags": service.list(task_id)}, status_code=201)

    async def list_tags(request: Request, task_id: str) -> Response:
        if not task_exists(task_id, request):
            return not_found(task_id)
        return JSONResponse({"task_id": task_id, "tags": service.list(task_id)})

    # 注册路由并登记可逆 disposer（HMR 重载时整体替换）。
    undos = [
        routes.add("POST", "/api/tasks/{task_id}/tags", add_tag, name="tags.add"),
        routes.add("GET", "/api/tasks/{task_id}/tags", list_tags, name="tags.list"),
    ]

    def undo_all() -> None:
        for undo in undos:
            undo()

    ctx.effect(lambda: undo_all)
    # 提供带版本的标签服务：消费方可经 @require 声明版本/接口约束（见 tests）。
    ctx.provide("tags", service, version="1.0")
