"""文件监听器：把文件系统变更自动接入 HMR 重载。"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .errors import AsyncRequiredError
from .hmr import HMR
from .utils import has_running_loop

__all__ = ["ConfigWatcher", "HMRWatcher"]

DEFAULT_IGNORED = ("**/.*", "**/__pycache__", "**/node_modules", "**/.venv", "cache", "data")

# 观察后端的最小协议：start() / stop()。
Backend = Any


def _default_on_error(path: str, error: Exception) -> None:
    """默认错误回调：打印到 stderr。"""
    print(f"[cordis_py.hmr] reload {path} failed: {error}", file=sys.stderr)


class HMRWatcher:
    """文件监听器：把源码变更接入 :class:`HMR` 的事务式重载。

    事件管道与观察后端解耦：``notify()`` 是线程安全的事件入口（watchdog 线程回调），
    事件经 ``loop.call_soon_threadsafe`` 桥接回事件循环后统一过滤、合并（debounce）并
    触发 ``hmr.reload_file``。重载失败通过 ``on_error`` 回调上报，观察持续进行。
    """

    def __init__(
        self,
        hmr: HMR,
        *,
        roots: Sequence[str | Path] = (".",),
        ignored: Sequence[str] = DEFAULT_IGNORED,
        debounce: float = 0.1,
        recursive: bool = True,
        backend: Backend | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self.hmr = hmr
        self.roots = [str(Path(root).expanduser().resolve()) for root in roots]
        self.ignored = tuple(ignored)
        self.debounce = debounce
        self.recursive = recursive
        self._backend = backend
        self._on_error = on_error or _default_on_error
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._stopped = True

    @property
    def running(self) -> bool:
        """观察器是否正在运行。"""
        return not self._stopped

    def start(self) -> HMRWatcher:
        """启动观察（需要运行中的事件循环），返回自身便于链式调用。"""
        if not has_running_loop():
            raise AsyncRequiredError("文件监听器的启动")
        self._loop = asyncio.get_running_loop()
        if self._backend is None:
            self._backend = _watchdog_backend(self.roots, self.recursive, self.notify)
        self._stopped = False
        self._backend.start()
        return self

    def notify(self, kind: str, path: str) -> None:
        """线程安全的事件入口：由观察后端回调。"""
        if self._stopped or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._accept, kind, path)

    def _accept(self, kind: str, path: str) -> None:
        """主线程侧的简单过滤：只有未忽略的 ``.py`` 变更进入 pending。"""
        if self._stopped:
            return
        if kind not in ("changed", "created"):
            return
        if not path.endswith(".py"):
            return
        if self._is_ignored(path):
            return
        self._pending.add(path)
        if self._task is None:
            self._task = self._loop.create_task(self._drain())

    async def _drain(self) -> None:
        """debounce 窗口后处理所有 pending 路径（失败不中断观察）。"""
        try:
            await asyncio.sleep(self.debounce)
            while self._pending and not self._stopped:
                pending = sorted(self._pending)
                self._pending.clear()
                for path in pending:
                    try:
                        await self.hmr.reload_file(path)
                    except Exception as error:  # noqa: BLE001 - 失败回滚后继续观察
                        self._on_error(path, error)
        finally:
            self._task = None

    async def stop(self) -> None:
        """停止观察并取消在途刷新任务。"""
        if self._stopped:
            return
        self._stopped = True
        try:
            self._backend.stop()
        finally:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None

    def _is_ignored(self, path: str) -> bool:
        """对齐 Node 的忽略语义：glob 匹配相对根目录的路径或目录名。"""
        target = os.path.normpath(path)
        # 目录名匹配：忽略路径中任意一段命中 pattern 的目录（如 .venv/node_modules）。
        parts = target.replace("\\", "/").split("/")
        for pattern in self.ignored:
            if fnmatch.fnmatch(target, pattern) or fnmatch.fnmatch(Path(target).name, pattern):
                return True
            for part in parts:
                if fnmatch.fnmatch(part, pattern.removeprefix("**/")):
                    return True
        return False


def _watchdog_backend(roots: Sequence[str], recursive: bool, notify: Callable[..., None]) -> Backend:
    """watchdog 观察后端：把文件事件转为 ``notify(kind, path)`` 回调。"""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:
        raise ImportError(
            "watchdog is required for file watching; install cordis-python[watch]"
        ) from exc

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event: Any) -> None:
            if event.is_directory:
                return
            notify("changed", event.src_path)

        def on_created(self, event: Any) -> None:
            if event.is_directory:
                return
            notify("created", event.src_path)

        def on_moved(self, event: Any) -> None:
            # 编辑器的原子保存（临时文件 + rename）不产生 modified/create，
            # 只有 moved 事件：以目标路径视为一次“新建”（v2 语义）。
            if event.is_directory:
                return
            notify("created", event.dest_path)

    observer = Observer()
    handler = _Handler()
    for root in roots:
        observer.schedule(handler, root, recursive=recursive)
    return observer


class ConfigWatcher:
    """配置文件监听器：把配置文件变更接入 :meth:`HMR.reload_config`。

    与 :class:`HMRWatcher` 的分工：

    - ``HMRWatcher``（``hmr.watch``）重载 ``.py`` 源码模块（模块级热替换）；
    - ``ConfigWatcher``（``hmr.watch_config``）监听装配配置文件（app.yml /
      app.json / app.toml），变更后经 Loader 的增量协调（``fiber.update()``）
      更新对应条目的 config，无需重启进程。

    事件管线与 HMRWatcher 一致：观察后端线程回调 → ``notify()``（线程安全）
    → 桥接回事件循环 → 按精确路径过滤 → debounce 合并 → ``reload_config``。
    过滤规则：只接受“目标配置文件路径完全一致”的 changed/created 事件；
    moved（原子保存）以目标路径视为 created。
    """

    def __init__(
        self,
        hmr: HMR,
        *,
        targets: Sequence[str | Path],
        debounce: float = 0.1,
        backend: Backend | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if not targets:
            raise ValueError("ConfigWatcher requires at least one target file")
        self.hmr = hmr
        self.targets = {str(Path(t).expanduser().resolve()) for t in targets}
        self.debounce = debounce
        self._backend = backend
        self._on_error = on_error or _default_on_error
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._stopped = True

    @property
    def running(self) -> bool:
        """观察器是否正在运行。"""
        return not self._stopped

    def start(self) -> ConfigWatcher:
        """启动观察（需要运行中的事件循环）。"""
        if not has_running_loop():
            raise AsyncRequiredError("配置文件的监听启动")
        self._loop = asyncio.get_running_loop()
        if self._backend is None:
            roots = sorted({str(Path(t).parent) for t in self.targets})
            self._backend = _watchdog_backend(roots, recursive=False, notify=self.notify)
        self._stopped = False
        self._backend.start()
        return self

    def notify(self, kind: str, path: str) -> None:
        """线程安全的事件入口：由观察后端回调。"""
        if self._stopped or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._accept, kind, path)

    def _accept(self, kind: str, path: str) -> None:
        """主线程侧过滤：只接受目标配置文件的 changed/created 事件。"""
        if self._stopped:
            return
        if kind not in ("changed", "created"):
            return
        if str(Path(path).resolve()) not in self.targets:
            return
        self._pending.add(path)
        if self._task is None:
            self._task = self._loop.create_task(self._drain())

    async def _drain(self) -> None:
        """debounce 窗口后处理所有 pending 路径（失败不中断观察）。"""
        try:
            await asyncio.sleep(self.debounce)
            while self._pending and not self._stopped:
                pending = sorted(self._pending)
                self._pending.clear()
                for path in pending:
                    try:
                        await self.hmr.reload_config(path)
                    except Exception as error:  # noqa: BLE001 - 失败后继续观察
                        self._on_error(path, error)
        finally:
            self._task = None

    async def stop(self) -> None:
        """停止观察并取消在途刷新任务。"""
        if self._stopped:
            return
        self._stopped = True
        try:
            self._backend.stop()
        finally:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
