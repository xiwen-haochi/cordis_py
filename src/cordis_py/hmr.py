"""开发期基础热模块替换辅助工具。"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from typing import Any

from .loader import Loader, import_string


def module_name_from_url(url: str) -> str:
    """从 ``module:attr`` 形式的 URL 中提取可导入模块名。"""
    if ":" in url:
        return url.split(":", 1)[0]
    # 兼容 ``package.module.attr`` 形式。
    return url.rpartition(".")[0]


def _remove_cached_pyc(module: Any) -> None:
    """删除模块的字节码缓存文件，强制重新编译。"""
    filename = getattr(module, "__file__", None)
    if not filename or not filename.endswith(".py"):
        return
    try:
        cache = importlib.util.cache_from_source(filename)
    except ValueError:
        return
    if os.path.exists(cache):
        os.remove(cache)


def reload_module(url: str) -> Any:
    """重新加载插件 URL 对应的模块，并返回新的属性。"""
    module_name = module_name_from_url(url)
    if module_name not in sys.modules:
        return import_string(url)
    module = sys.modules[module_name]
    _remove_cached_pyc(module)
    module = importlib.reload(module)
    if ":" in url:
        return getattr(module, url.split(":", 1)[1])
    return getattr(module, url.rpartition(".")[2])


class HMR:
    """面向开发期的 Loader 条目热重载。

    这里刻意保持最小实现：一次只重载一个条目，不做完整的依赖图分类。
    适合开发期内部依赖较少的插件使用。
    """

    def __init__(self, loader: Loader) -> None:
        self.loader = loader

    async def reload_entry(self, entry_id: str) -> None:
        """卸载并重新加载单个 Loader 条目。"""
        entry = self.loader.entries.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        fiber = self.loader.fibers.pop(entry_id, None)
        if fiber is not None:
            await fiber.dispose()
        # 重新导入模块，确保使用新代码。
        reload_module(entry.url)
        await self.loader.enable(entry_id)

    async def reload_all(self) -> None:
        """卸载并重新加载所有 Loader 管理的条目。"""
        for entry_id in list(self.loader.entries):
            await self.reload_entry(entry_id)


__all__ = ["HMR", "reload_module"]
