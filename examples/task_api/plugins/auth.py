"""认证插件：X-Tenant / X-API-Key 校验并绑定请求租户。"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from cordis_py import Context


class AuthService:
    """API Key 认证：通过后写入 ``request.state.tenant``。"""

    def __init__(self, keys: dict[str, str], public_paths: frozenset[str] = frozenset()) -> None:
        self._keys = keys
        self._public_paths = public_paths

    def authenticate(self, request: Request) -> JSONResponse | None:
        if request.url.path in self._public_paths:
            # 公开路径（如健康检查）：免认证直通。
            return None
        tenant = request.headers.get("x-tenant")
        key = request.headers.get("x-api-key")
        if tenant and self._keys.get(tenant) == key:
            request.state.tenant = tenant
            return None
        return JSONResponse(
            {"error": "unauthorized", "tenant": tenant},
            status_code=401,
            headers={"www-authenticate": "X-API-Key"},
        )


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """注册 ``auth`` 服务（http 中间件缺省感知其存在）。"""
    public = frozenset(str(p) for p in config.get("public_paths") or ())
    ctx.provide("auth", AuthService(dict(config.get("keys") or {}), public))
