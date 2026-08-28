"""HTTP 装配插件：把 FastAPI 应用接入 Cordis 生命周期。

提供服务：

- ``routes``：可逆路由注册表（业务插件用它注册/替换/卸载路由，HMR 安全）；
- ``app``：FastAPI 应用实例。

请求链路：

1. 认证（``auth`` 服务存在时校验，失败直接返回 401）；
2. ``http/request`` 瀑布（限流等插件作为中间件环，不调用 ``next`` 即拦截）；
3. 链尾 = 真实请求处理（``call_next``）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from cordis_py import Context, inject


class CordisGateMiddleware(BaseHTTPMiddleware):
    """固定的中间件入口：装配前/后请求直通，装配后由插件注册的 gate 处理。

    中间件结构由宿主静态安装（不随插件装载变动），具体链路逻辑（认证 →
    瀑布链 → 真实处理）由 ``http`` 插件在 lifespan 内注册到
    ``app.state.cordis_gate``，可随插件卸载整体移除。
    """

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        gate = getattr(request.app.state, "cordis_gate", None)
        if gate is None:
            return await call_next(request)
        return await gate(request, call_next)


class RouteRegistry:
    """FastAPI 路由注册表：同名 (method, path) 可整体替换（重载安全）。"""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._routes: dict[tuple[str, str], Any] = {}

    def add(
        self,
        method: str,
        path: str,
        handler: Callable[..., Any],
        *,
        name: str | None = None,
    ) -> Callable[[], None]:
        """注册路由并返回卸载函数；已存在同名路由时先移除旧条目。"""
        key = (method.upper(), path)
        old = self._routes.get(key)
        if old is not None and old in self._app.routes:
            self._app.routes.remove(old)
        route = self._app.add_api_route(
            path, handler, methods=[method.upper()], name=name or handler.__name__
        )
        self._routes[key] = route

        def undo() -> None:
            if self._routes.get(key) is route:
                self._routes.pop(key, None)
                if route in self._app.routes:
                    self._app.routes.remove(route)

        return undo


@inject(["fastapi_app", "log"])
def plugin(ctx: Context, config: dict[str, Any]) -> None:
    """装配：gate 链路（认证 + 请求瀑布）+ 路由注册表 + 应用服务。"""
    app: FastAPI = ctx.fastapi_app
    logger = ctx.log("http")
    routes = RouteRegistry(app)

    async def gate(request: Request, call_next: Callable[..., Any]) -> Response:
        # 认证插件可缺省：不存在时直通。
        auth = ctx.get("auth")
        if auth is not None:
            rejected = auth.authenticate(request)
            if rejected is not None:
                return rejected
        tenant = getattr(request.state, "tenant", None)
        # 瀑布链：插件中途拦截（如限流）则直接返回；链尾执行真实处理。
        return await ctx.waterfall(
            "http/request",
            tenant,
            request,
            fallback=lambda *args: call_next(request),
        )

    # gate 由宿主中间件在请求期读取；插件卸载时移除（中间件恢复直通）。
    app.state.cordis_gate = gate

    def undo() -> None:
        if getattr(app.state, "cordis_gate", None) is gate:
            delattr(app.state, "cordis_gate")

    ctx.effect(lambda: undo)

    ctx.provide("routes", routes)
    ctx.provide("app", app)
    logger.info("http service ready: %s", config.get("title", "Cordis API"))
