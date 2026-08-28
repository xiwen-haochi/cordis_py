"""多租户任务 API 案例：FastAPI + cordis_py 插件化装配入口。"""

from __future__ import annotations

import argparse
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

BASE = Path(__file__).resolve().parent

sys.path.append(str(BASE / "src"))

from cordis_py import HMR, Context, Loader


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：宿主注入应用对象 → Loader 声明式装配 → 可选 HMR 监听。"""
    root = Context()
    # 运行时对象（FastAPI 应用）由宿主注入，不进配置文件。
    root.provide("fastapi_app", app)
    loader = Loader(root)
    await loader.include(BASE / "app.yml")
    app.state.cordis = root

    hmr: HMR | None = None
    if getattr(app.state, "watch", False):
        hmr = HMR(loader)
        app.state.hmr_watcher = hmr.watch([str(BASE / "plugins")])

    yield
    # 退出：可逆效果按 LIFO 全部回收（服务、路由、监听器、模块追踪器）。
    if hmr is not None:
        await app.state.hmr_watcher.stop()
        hmr.dispose()
    await loader.dispose()
    await root.fiber.dispose()


def make_app(watch: bool = False) -> FastAPI:
    """创建应用；*watch* 为 True 时启用插件源码热替换。"""
    app = FastAPI(title="Cordis Task API", version="0.1.0", lifespan=lifespan)
    # 静态安装中间件入口（不随插件装载变动），链路逻辑由 http 插件注册。
    from plugins.http_service import CordisGateMiddleware

    app.add_middleware(CordisGateMiddleware)
    app.state.watch = watch
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Cordis 插件化任务 API 案例")
    parser.add_argument(
        "--watch", action="store_true", default=True, help="启用 HMR 插件源码热替换"
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(make_app(watch=args.watch), host="127.0.0.1", port=args.port)


app = make_app()

if __name__ == "__main__":
    main()
