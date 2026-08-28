"""JWT 认证插件：与 auth 插件同契约（authenticate），可无缝替换。

标准库实现 HS256 JWT（不引入第三方依赖），展示“同契约不同实现”的插件替换：
http 插件的 gate 只消费 ``ctx.get("auth")`` 的 ``authenticate(request)`` 接口，
因此把 app.yml 中的 auth 插件行换成本插件即可切换认证方式，其余插件零改动。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from cordis_py import Context


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class JwtAuthService:
    """HS256 JWT 认证：通过后按 ``sub`` 写入 ``request.state.tenant``。"""

    def __init__(
        self,
        secret: str,
        issuer: str = "cordis-task-api",
        audience: str = "cordis-task-api",
        public_paths: frozenset[str] = frozenset(),
    ) -> None:
        self._secret = secret.encode("utf-8")
        self._issuer = issuer
        self._audience = audience
        self._public_paths = public_paths

    def issue(self, tenant: str, *, ttl: int = 3600) -> str:
        """签发（管理端/测试用）：HS256 三段 JWT。"""
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": tenant,
            "iss": self._issuer,
            "aud": self._audience,
            "exp": int(time.time()) + ttl,
        }
        signing = b".".join(
            (_b64(json.dumps(header).encode()), _b64(json.dumps(payload).encode()))
        )
        signature = hmac.new(self._secret, signing, hashlib.sha256).digest()
        return (signing + b"." + _b64(signature)).decode()

    def _reject(self, error: str, status: int = 401) -> JSONResponse:
        return JSONResponse({"error": error}, status_code=status)

    def authenticate(self, request: Request) -> JSONResponse | None:
        if request.url.path in self._public_paths:
            return None
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return self._reject("missing_token")
        parts = header[7:].split(".")
        if len(parts) != 3:
            return self._reject("invalid_token")
        signing = f"{parts[0]}.{parts[1]}".encode()
        expected = hmac.new(self._secret, signing, hashlib.sha256).digest()
        try:
            signature = _unb64(parts[2])
        except Exception:  # noqa: BLE001 - 恶意输入走统一 401
            return self._reject("invalid_token")
        if not hmac.compare_digest(signature, expected):
            return self._reject("invalid_token")
        try:
            payload = json.loads(_unb64(parts[1]))
        except Exception:  # noqa: BLE001
            return self._reject("invalid_token")
        if payload.get("iss") != self._issuer or payload.get("aud") != self._audience:
            return self._reject("invalid_token")
        if int(payload.get("exp", 0)) < time.time():
            return self._reject("token_expired")
        request.state.tenant = str(payload.get("sub") or "")
        return None


def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """注册 ``auth`` 服务（同名契约，替换 api-key 认证）。"""
    service = JwtAuthService(
        secret=str(config.get("secret") or "change-me"),
        issuer=str(config.get("issuer") or "cordis-task-api"),
        audience=str(config.get("audience") or "cordis-task-api"),
        public_paths=frozenset(str(p) for p in config.get("public_paths") or ()),
    )
    ctx.provide("auth", service)
