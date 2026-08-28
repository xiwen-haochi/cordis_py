"""租户插件：为每个租户建立独立 realm 并注册专属任务存储。"""

from __future__ import annotations

from typing import Any

from cordis_py import Context


class TaskStore:
    """单租户内存任务存储（可替换为数据库实现）。"""

    def __init__(self, tenant: str) -> None:
        self.tenant = tenant
        self._tasks: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def create(self, title: str, priority: int = 1) -> dict[str, Any]:
        self._seq += 1
        task = {
            "id": f"{self.tenant}-{self._seq}",
            "title": title,
            "priority": priority,
            "status": "open",
            "tenant": self.tenant,
        }
        self._tasks[task["id"]] = task
        return task

    def list(self) -> list[dict[str, Any]]:
        return list(self._tasks.values())

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def delete(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.pop(task_id, None)


class TenantRegistry:
    """租户注册表：按租户名给出隔离作用域内的任务存储。"""

    def __init__(self) -> None:
        self._scopes: dict[str, Context] = {}

    def register(self, ctx: Context, name: str) -> None:
        """为 *name* 建立独立 realm 的服务作用域，并在其中提供 tasks 存储。"""
        scoped = ctx.isolate("tasks", name)
        store = TaskStore(name)
        # (realm=name, name="tasks") 与其它租户互不可见。
        scoped.provide("tasks", store)
        self._scopes[name] = scoped

    def store(self, name: str) -> TaskStore:
        """经该租户的隔离作用域解析任务存储（realm 服务查找）。"""
        scoped = self._scopes[name]
        return scoped.get("tasks")

    def tenants(self) -> list[str]:
        return sorted(self._scopes)


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """按配置注册全部租户；registry 服务带版本号供契约校验。"""
    registry = TenantRegistry()
    tenants = list(config.get("tenants") or ())
    for name in tenants:
        registry.register(ctx, name)
    ctx.provide("tenants", registry, version="1.0")
