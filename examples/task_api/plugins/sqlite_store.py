"""SQLite 持久化插件：把任务数据落盘（重启不丢失）。

与内存 TaskStore 同契约（``create`` / ``list`` / ``get`` / ``delete`` +
``tenant`` 属性），因此对业务插件透明：tenant 插件通过 ``ctx.get("sqlite")``
可选地使用本服务，未装配时自动回退内存实现（原行为不变）。

存储为唯一真相源：每次操作直接读写 SQLite，不保留内存副本，
进程重启后数据天然仍在。

契约：
- ``create(title, priority=1) -> dict{id,title,priority,status,tenant}``
- ``list() -> list[dict]``（按创建序）
- ``get(task_id) -> dict | None``
- ``delete(task_id) -> dict | None``
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from cordis_py import Context

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    tenant   TEXT    NOT NULL,
    seq      INTEGER NOT NULL,
    id       TEXT    NOT NULL,
    title    TEXT    NOT NULL,
    priority INTEGER NOT NULL DEFAULT 1,
    status   TEXT    NOT NULL DEFAULT 'open',
    PRIMARY KEY (tenant, seq),
    UNIQUE (tenant, id)
)
"""


class SqliteDB:
    """SQLite 数据库封装：连接 + 表初始化 + 按租户取存储。

    连接由本对象管理：``close()`` 只弃用当前连接；后续操作发现连接
    已关闭时自动重开（惰性重连）——这保证配置热更替换本插件后，
    仍持有旧 store 引用的消费方（如已装配的 tenant 插件）不因
    "旧连接已关闭"而报错，而会透明地重新打开数据库文件。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.execute(_SCHEMA)
        conn.commit()
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        """当前连接；已关闭（或从未打开）时惰性重开。"""
        if self._conn is None:
            self._open()
        return self._conn

    def close(self) -> None:
        """弃用当前连接（下次操作自动重开，进程退出时无后续操作=真正关闭）。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def store(self, tenant: str) -> SqliteStore:
        """返回该租户的存储；方法内直接读写 SQLite（无内存副本）。"""
        return SqliteStore(self, tenant)


class SqliteStore:
    """单租户任务存储（SQLite 后端）：与内存 TaskStore 同契约。"""

    def __init__(self, db: SqliteDB, tenant: str) -> None:
        self.tenant = tenant
        self._db = db

    def _next_seq(self) -> int:
        """自增序号从库中恢复：重启后新任务 id 与旧任务不冲突。"""
        row = self._db.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM tasks WHERE tenant = ?", (self.tenant,)
        ).fetchone()
        return int(row[0])

    def create(self, title: str, priority: int = 1) -> dict[str, Any]:
        seq = self._next_seq()
        task = {
            "id": f"{self.tenant}-{seq}",
            "title": title,
            "priority": priority,
            "status": "open",
            "tenant": self.tenant,
        }
        self._db.conn.execute(
            "INSERT INTO tasks (tenant, seq, id, title, priority, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (self.tenant, seq, task["id"], task["title"], task["priority"], task["status"]),
        )
        self._db.conn.commit()
        return task

    def list(self) -> list[dict[str, Any]]:
        rows = self._db.conn.execute(
            "SELECT id, title, priority, status, tenant FROM tasks"
            " WHERE tenant = ? ORDER BY seq",
            (self.tenant,),
        ).fetchall()
        return [
            {
                "id": row[0],
                "title": row[1],
                "priority": row[2],
                "status": row[3],
                "tenant": row[4],
            }
            for row in rows
        ]

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.conn.execute(
            "SELECT id, title, priority, status, tenant FROM tasks"
            " WHERE tenant = ? AND id = ?",
            (self.tenant, task_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "priority": row[2],
            "status": row[3],
            "tenant": row[4],
        }

    def delete(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        if task is None:
            return None
        self._db.conn.execute(
            "DELETE FROM tasks WHERE tenant = ? AND id = ?", (self.tenant, task_id)
        )
        self._db.conn.commit()
        return task


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """注册 ``sqlite`` 服务（可选）：tenant 插件未装配时回退内存存储。

    config（可选）：
    - ``path``：数据库文件路径；缺省为示例目录下的 ``tasks.db``。
      测试可用环境变量 ``CORDIS_SQLITE_PATH`` 覆盖（优先级：env > config > 默认）。
    """
    path = (
        os.environ.get("CORDIS_SQLITE_PATH")
        or config.get("path")
        or str(Path(__file__).resolve().parent.parent / "tasks.db")
    )
    db = SqliteDB(path)
    # 应用退出时关闭连接（可逆效果：LIFO 回收）。
    ctx.effect(lambda: db.close)
    ctx.provide("sqlite", db)
# HMR 心跳: 时间戳 $(date +%s)
