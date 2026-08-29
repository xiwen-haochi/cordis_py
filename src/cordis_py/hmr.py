"""开发期热模块替换：依赖图分类与事务式重载。"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from .depgraph import Classification, ModuleGraph
from .loader import Loader, import_string

__all__ = ["HMR", "module_name_from_url"]


def module_name_from_url(url: str) -> str:
    """从 ``module:attr`` 形式的 URL 中提取可导入模块名。"""
    if ":" in url:
        return url.split(":", 1)[0]
    # 兼容 ``package.module.attr`` 形式。
    return url.rpartition(".")[0]


def _remove_cached_pyc(module: ModuleType) -> None:
    """删除模块的字节码缓存，防止同秒内同 mtime 的改动命中旧 pyc。"""
    filename = getattr(module, "__file__", None)
    if not filename or not filename.endswith(".py"):
        return
    try:
        cache = importlib.util.cache_from_source(filename)
    except ValueError:
        return
    if os.path.exists(cache):
        os.remove(cache)


def _reloadable(module: ModuleType) -> bool:
    """仅纯 Python 源码模块可热重载（扩展模块 / 命名空间包跳过）。"""
    file = getattr(module, "__file__", None)
    return bool(file) and file.endswith(".py")


class HMR:
    """面向开发期的 Loader 条目热重载。

    流程与 Node Cordis v4 的 HMR 对齐：

    1. **模块分类**：对变更模块集合计算 accepted / declined 分类；
    2. **过期条目检测**：找出依赖闭包触及 accepted 的 Loader 条目；
    3. **事务式重载**：快照旧插件与模块命名空间，dispose 旧 fiber，按“依赖先于
       导入者”的拓扑序重载模块，再以新插件对象重新 apply；任一步失败则恢复命名
       空间快照并用旧插件重新 apply（回滚）后重抛。
    """

    def __init__(self, loader: Loader, *, graph: ModuleGraph | None = None) -> None:
        self.loader = loader
        self._owns_graph = graph is None
        self._graph = graph if graph is not None else ModuleGraph()
        self._graph.install()
        self._disposed = False

    @property
    def graph(self) -> ModuleGraph:
        """本 HMR 使用的模块依赖图。"""
        return self._graph

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------

    def affected(self, changed: set[str]) -> list[str]:
        """预测：给定变更模块集合，哪些条目会被重载（保持声明顺序）。"""
        affected, _ = self._compute_affected(changed)
        return affected

    def _compute_affected(self, changed: set[str]) -> tuple[list[str], Classification]:
        """计算分类结果与受影响条目（与 Node partialReload 插件轮对齐）。

        注意：Node 在插件轮会把条目依赖闭包并入 accepted（供 ESM 缓存失效使用），
        Python 的重载集是显式的（变更模块 + 条目模块），不变化依赖保持缓存，
        因此这里不加膨胀，避免无关但共享依赖的条目被连带重载。
        """
        classification = self._graph.classify(changed)
        accepted = set(classification.accepted)
        declined = set(classification.declined)
        affected: list[str] = []
        for entry in self.loader.entries.values():
            if entry.disabled:
                continue
            module = module_name_from_url(entry.url)
            if module in declined:
                continue
            dependencies = self._graph.closure(module, skip=declined)
            if dependencies & accepted:
                affected.append(entry.id)
        return affected, classification

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    async def reload_entry(self, entry_id: str) -> list[str]:
        """重新加载条目；现在会连带重载依赖其模块的其他条目。

        对禁用条目的显式重载保留旧语义：重载其模块并重新启用。
        """
        entry = self.loader.entries.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        module_name = module_name_from_url(entry.url)
        affected = await self._reload({module_name})
        if entry_id not in affected:
            module = sys.modules.get(module_name)
            if isinstance(module, ModuleType) and _reloadable(module):
                _remove_cached_pyc(module)
                importlib.reload(module)
            await self.loader.enable(entry_id)
            return [entry_id]
        return affected

    async def reload_module(self, module_name: str) -> list[str]:
        """重新加载指定模块，并重载所有受影响的条目。

        模块尚未加载时先导入（此时其内部边经追踪器记录）。
        """
        if module_name not in sys.modules:
            importlib.import_module(module_name)
        return await self._reload({module_name})

    async def reload_file(self, path: str | Path) -> list[str]:
        """按变更文件重载；文件未被任何已加载模块对应时为空操作。

        适合供文件监听器直接调用：非 Python 文件或尚未导入的源码文件会被自然忽略。
        """
        module = self._graph.file_to_module(str(path))
        if module is None:
            return []
        return await self._reload({module})

    async def reload_all(self) -> list[str]:
        """重新加载所有非禁用条目。"""
        changed = {
            module_name_from_url(entry.url)
            for entry in self.loader.entries.values()
            if not entry.disabled
        }
        return await self._reload(changed)

    # ------------------------------------------------------------------
    # 配置文件热更（与源码 HMR 并列的第二种“开发期热更新”）
    # ------------------------------------------------------------------

    async def reload_config(self, path: str | Path) -> list[str]:
        """重新读取配置文件并增量协调（配置热更，进程不重启）。

        经 :meth:`Loader.include` → ``reconcile``：仅 config 发生变化的条目
        执行 ``fiber.update()``（撤销旧效果、按新配置重启）；条目增删、
        启停（disabled）变化同样被协调。

        :return: config 或 disabled 状态发生变化的条目 id 列表（增量，无变化为 []）。
        """
        if self._disposed:
            raise RuntimeError("HMR has been disposed")
        before = {
            eid: (dict(entry.config), entry.disabled)
            for eid, entry in self.loader.entries.items()
        }
        await self.loader.include(path)
        after = {
            eid: (dict(entry.config), entry.disabled)
            for eid, entry in self.loader.entries.items()
        }
        return [eid for eid in after if before.get(eid) != after[eid]]

    def watch_config(
        self,
        targets: Sequence[str | Path],
        *,
        debounce: float = 0.1,
        backend: Any | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> Any:
        """启动配置文件监听，把配置文件变更自动接入 :meth:`reload_config`。

        *targets* 为配置文件路径列表（监听其父目录、按精确路径过滤）。
        需要运行中的事件循环；未安装 watchdog 时抛出带安装提示的 ImportError。

        与 :meth:`watch` 的分工：``watch`` 重载 ``.py`` 源码模块；本方法走
        Loader 的增量协调（``fiber.update()``），适合 app.yml 这类装配配置。
        """
        from .watcher import ConfigWatcher

        return ConfigWatcher(
            self,
            targets=targets,
            debounce=debounce,
            backend=backend,
            on_error=on_error,
        ).start()

    def dispose(self) -> None:
        """卸载模块图追踪器（幂等）。"""
        if self._disposed:
            return
        self._disposed = True
        if self._owns_graph:
            self._graph.uninstall()

    def watch(
        self,
        roots: Sequence[str | Path] = (".",),
        *,
        ignored: Sequence[str] | None = None,
        debounce: float = 0.1,
        recursive: bool = True,
        backend: Any | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> Any:
        """启动文件监听器，把源码变更自动接入热重载。

        需要运行中的事件循环（watchdog 在独立线程观察，事件桥接回 asyncio）。
        未安装 watchdog 时抛出带安装提示的 ImportError。
        """
        from .watcher import DEFAULT_IGNORED, HMRWatcher

        return HMRWatcher(
            self,
            roots=roots,
            ignored=ignored if ignored is not None else DEFAULT_IGNORED,
            debounce=debounce,
            recursive=recursive,
            backend=backend,
            on_error=on_error,
        ).start()

    # ------------------------------------------------------------------
    # 事务
    # ------------------------------------------------------------------

    async def _reload(self, changed: set[str]) -> list[str]:
        if self._disposed:
            raise RuntimeError("HMR has been disposed")
        affected, _ = self._compute_affected(changed)
        if not affected:
            return []
        # 重载目标：直接变更的模块 + 受影响条目的模块（刷新 from-import 绑定）。
        # 未变更的中间依赖保持缓存即可——Python 的 importlib.reload 需要显式指定
        # 才重执行模块，全闭包重载只会无谓地触发模块级副作用。
        reload_targets = set(changed)
        for entry_id in affected:
            reload_targets.add(module_name_from_url(self.loader.entries[entry_id].url))

        # 1. 快照：旧插件对象与将被重载模块的命名空间。
        old_plugins: dict[str, Any] = {
            entry_id: self.loader.fibers[entry_id].plugin for entry_id in affected
        }
        snapshots: dict[str, tuple[ModuleType, dict[str, Any]]] = {}
        for name in reload_targets:
            module = sys.modules.get(name)
            if isinstance(module, ModuleType) and _reloadable(module):
                snapshots[name] = (module, dict(module.__dict__))

        # 2. 卸载受影响条目。
        for entry_id in affected:
            fiber = self.loader.fibers.pop(entry_id, None)
            if fiber is not None:
                await fiber.dispose()

        applied: list[str] = []
        try:
            # 3. 按“依赖先于导入者”的拓扑序重载目标模块。
            reloaded: set[str] = set()
            for name in self._topo_order(reload_targets):
                module = sys.modules.get(name)
                if isinstance(module, ModuleType) and _reloadable(module):
                    _remove_cached_pyc(module)
                    importlib.reload(module)
                    reloaded.add(name)

            # 4. 解析新插件对象并按声明顺序重新应用。
            new_plugins: dict[str, Any] = {
                entry_id: import_string(self.loader.entries[entry_id].url)
                for entry_id in affected
            }
            for entry_id in affected:
                entry = self.loader.entries[entry_id]
                await self.loader._start_entry(entry, plugin=new_plugins[entry_id])
                applied.append(entry_id)
        except Exception:
            await self._rollback(affected, applied, old_plugins, snapshots)
            raise
        return affected

    async def _rollback(
        self,
        affected: list[str],
        applied: list[str],
        old_plugins: dict[str, Any],
        snapshots: dict[str, tuple[ModuleType, dict[str, Any]]],
    ) -> None:
        """事务回滚：卸掉新 fiber、恢复模块命名空间、用旧插件重新应用。"""
        for entry_id in applied:
            fiber = self.loader.fibers.pop(entry_id, None)
            if fiber is not None:
                await fiber.dispose()
        for name, (module, old) in snapshots.items():
            target = sys.modules.get(name) or module
            target.__dict__.clear()
            target.__dict__.update(old)
        for entry_id in affected:
            entry = self.loader.entries[entry_id]
            try:
                await self.loader._start_entry(entry, plugin=old_plugins[entry_id])
            except Exception:  # noqa: BLE001, S110 - 回滚失败不掩盖原始异常
                pass

    def _topo_order(self, names: set[str]) -> list[str]:
        """按“依赖先于导入者”的拓扑序排列模块名（DFS 防环）。"""
        order: list[str] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            for child in sorted(self._graph.imports(name)):
                if child in names:
                    visit(child)
            order.append(name)

        for name in sorted(names):
            visit(name)
        return order
