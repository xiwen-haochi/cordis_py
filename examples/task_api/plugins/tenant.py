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
        self._ctx: Context | None = None
        self._mem_stores: dict[str, TaskStore] = {}

    def register(self, ctx: Context, name: str) -> None:
        """为 *name* 建立独立 realm 的服务作用域（服务值即租户名）。

        存储的解析推迟到 :meth:`store` 调用时刻（动态解析当前 ``sqlite``
        服务）：SQLite 插件被配置热更/源码热替换后，旧 store 引用不会被
        固化 —— 消费方拿到的总是与当前装配一致的后端。
        内存模式的 store 也是按租户缓存的单例：同一「租户 + 后端」组合
        恒返回同一实例（与 SQLite 的 ``db.store(name)`` 语义一致）。
        """
        scoped = ctx.isolate("tasks", name)
        self._ctx = ctx
        # (realm=name, name="tasks") 与其它租户互不可见。
        scoped.provide("tasks", name)
        self._scopes[name] = scoped

    def store(self, name: str) -> Any:
        """经该租户的隔离作用域解析任务存储（realm 服务查找）。

        ``tasks`` 服务值是租户名；存储实例按当前装配动态构造：
        - 装配了 sqlite 插件 → SQLite 持久化存储（重启不丢失）；
        - 未装配 → 内存 TaskStore（原行为，契约一致）。
        """
        db = self._ctx.get("sqlite") if self._ctx is not None else None
        if db is not None:
            return db.store(name)
        store = self._mem_stores.get(name)
        if store is None:
            store = TaskStore(name)
            self._mem_stores[name] = store
        return store

    def tenants(self) -> list[str]:
        return sorted(self._scopes)


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """按配置注册全部租户；registry 服务带版本号供契约校验。"""
    registry = TenantRegistry()
    tenants = list(config.get("tenants") or ())
    for name in tenants:
        registry.register(ctx, name)
    ctx.provide("tenants", registry, version="1.0")
