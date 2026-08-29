"""失败监控插件：把失败的 HTTP 请求记录到 err.log。

放在 http/request 瀑布链上（注册顺序第一个 —— 用 prepend 保证），
通过调用 ``next_()`` 拿到下游（可能还有别的监听器/最终 call_next）
产生的响应，检查状态码：

- 响应 status_code >= 400 → 追加一条记录到 err.log；
- 下游抛出异常（如业务 500）→ 同样记录，然后继续抛出（不改写行为）；
- 正常响应（< 400）→ 不记录。

记录格式（每行一条）：``[时间] 方法 路径 -> 状态码 (tenant=...)``。

可逆性：监听器经 ``ctx.on`` 登记，插件卸载/HMR 重载时自动移除；
err.log 是追加式文件（不属于插件生命周期，卸载后保留历史）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from cordis_py import Context

_DEFAULT_LOG = "err.log"  # 相对路径：相对进程工作目录，一般 = 示例目录/err.log


def _append(path: Path, line: str) -> None:
    """追加一行（append 模式；每次打开写入后可被外部 tail 看到）。"""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """装配失败监控；config: ``path``（默认 err.log，相对工作目录）。

    环境变量 ``CORDIS_FAIL_LOG`` 可覆盖路径（优先级：env > config > 默认），
    便于测试隔离与多实例部署。
    """
    import os

    log_path = Path(
        os.environ.get("CORDIS_FAIL_LOG")
        or config.get("path")
        or _DEFAULT_LOG
    )
    logger = ctx.get("log")

    async def monitor(tenant: str | None, request: Request, next_: Any) -> Response:
        try:
            response = await next_()
        except Exception as exc:  # 监控记录后原样重抛（不改写业务行为）
            line = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"{request.method} {request.url.path} -> EXCEPTION {type(exc).__name__}: {exc} "
                f"(tenant={tenant})"
            )
            _append(log_path, line)
            if logger is not None:
                logger("failures").warning("记录失败请求到 %s", log_path)
            raise
        if response.status_code >= 400:
            line = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"{request.method} {request.url.path} -> {response.status_code} "
                f"(tenant={tenant})"
            )
            _append(log_path, line)
            if logger is not None:
                logger("failures").warning("记录失败请求到 %s", log_path)
        return response

    # prepend=True：挂到链首，next_() 才能拿到下游全部响应。
    ctx.on("http/request", monitor, prepend=True)
