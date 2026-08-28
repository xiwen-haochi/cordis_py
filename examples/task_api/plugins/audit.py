"""审计插件：监听业务事件并写入日志。"""

from __future__ import annotations

from typing import Any

from cordis_py import Context

_EVENTS = ("task/created", "task/deleted")


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    prefix = str(config.get("event_prefix", ""))
    logger = ctx.get("log")

    def audit(event: str, tenant: str, task: dict[str, Any]) -> None:
        if logger is not None:
            logger(f"audit.{event}").info(
                "%s tenant=%s task=%s", prefix, tenant, task.get("id")
            )

    for event in _EVENTS:
        # 监听器随本插件 fiber 回收（HMR 重载时自动清理）。
        # 注意默认参数绑定：避免闭包捕获循环变量。
        ctx.on(event, lambda *args, _event=event: audit(_event, *args))
