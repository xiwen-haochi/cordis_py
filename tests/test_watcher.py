from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from cordis_py import HMR, Context, HMRWatcher, Loader


def _unload(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """从 sys.modules 中卸载指定模块，避免跨测试串扰。"""
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)


class _FakeBackend:
    """可注入的最小观察后端：只记录启动/停止。"""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    """轮询等待条件成立（异步事件时序）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "等待超时"
        await asyncio.sleep(0.01)


def _write_fixture(tmp_path: Path, data_file: Path, plugin_file: Path, value: str) -> None:
    data_file.write_text(f"VALUE = {value!r}\n", encoding="utf-8")
    plugin_file.write_text(
        "import wb_counts\n"
        "from wb_data import VALUE\n\n"
        "wb_counts.A += 1\n\n"
        "def plugin(ctx, config):\n    ctx.provide('value', VALUE)\n",
        encoding="utf-8",
    )
    (tmp_path / "wb_counts.py").write_text("A = 0\n", encoding="utf-8")


async def test_watcher_reloads_on_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_file = tmp_path / "wb_data.py"
    plugin_file = tmp_path / "wb_plugin.py"
    _write_fixture(tmp_path, data_file, plugin_file, "v1")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "wb_counts", "wb_data", "wb_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "wb", "url": "wb_plugin:plugin", "config": {}}])
    assert root.services["value"] == "v1"

    hmr = HMR(loader)
    backend = _FakeBackend()
    watcher = hmr.watch([str(tmp_path)], backend=backend)
    assert watcher.running and backend.started
    try:
        data_file.write_text("VALUE = 'v2'\n", encoding="utf-8")
        # 编辑器原子保存场景：created 也应触发。
        watcher.notify("created", str(data_file))
        await _wait_for(lambda: root.services.get("value") == "v2")
        assert sys.modules["wb_counts"].A == 2
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_watcher_ignores_irrelevant_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_file = tmp_path / "wb_data.py"
    plugin_file = tmp_path / "wb_plugin.py"
    _write_fixture(tmp_path, data_file, plugin_file, "v1")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "wb_counts", "wb_data", "wb_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "wb", "url": "wb_plugin:plugin", "config": {}}])

    hmr = HMR(loader)
    watcher = hmr.watch([str(tmp_path)], backend=_FakeBackend())
    try:
        # 以下事件全部应被忽略：删除、非 .py、ignored 目录、隐藏目录。
        watcher.notify("deleted", str(data_file))
        watcher.notify("changed", str(tmp_path / "notes.txt"))
        watcher.notify("changed", str(tmp_path / "data" / "app.py"))
        watcher.notify("changed", str(tmp_path / ".venv" / "inner.py"))
        watcher.notify("changed", str(tmp_path / "sub" / "__pycache__" / "x.py"))
        await asyncio.sleep(0.25)
        assert sys.modules["wb_counts"].A == 1
        # 观察器仍然存活：真实变更依旧生效。
        data_file.write_text("VALUE = 'v2'\n", encoding="utf-8")
        watcher.notify("changed", str(data_file))
        await _wait_for(lambda: root.services.get("value") == "v2")
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_watcher_debounces_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_file = tmp_path / "wb_data.py"
    plugin_file = tmp_path / "wb_plugin.py"
    _write_fixture(tmp_path, data_file, plugin_file, "v1")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "wb_counts", "wb_data", "wb_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "wb", "url": "wb_plugin:plugin", "config": {}}])

    hmr = HMR(loader)
    watcher = hmr.watch([str(tmp_path)], backend=_FakeBackend(), debounce=0.2)
    try:
        data_file.write_text("VALUE = 'v2'\n", encoding="utf-8")
        watcher.notify("changed", str(data_file))
        await asyncio.sleep(0.05)
        watcher.notify("changed", str(data_file))
        await asyncio.sleep(0.05)
        watcher.notify("changed", str(data_file))
        # 三个事件落在同一 debounce 窗口内 → 只重载一次。
        await _wait_for(lambda: root.services.get("value") == "v2")
        await asyncio.sleep(0.25)
        assert sys.modules["wb_counts"].A == 2
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_watcher_error_callback_and_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_file = tmp_path / "wb_data.py"
    plugin_file = tmp_path / "wb_plugin.py"
    _write_fixture(tmp_path, data_file, plugin_file, "v1")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "wb_counts", "wb_data", "wb_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "wb", "url": "wb_plugin:plugin", "config": {}}])

    errors: list[tuple[str, Exception]] = []
    hmr = HMR(loader)
    watcher = hmr.watch(
        [str(tmp_path)],
        backend=_FakeBackend(),
        on_error=lambda path, error: errors.append((path, error)),
    )
    try:
        # 写入会抛错的插件代码：重载失败 → 回滚并上报，观察继续。
        plugin_file.write_text(
            "def plugin(ctx, config):\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        watcher.notify("changed", str(plugin_file))
        await _wait_for(lambda: len(errors) == 1)
        assert errors[0][0] == str(plugin_file)
        assert root.services["value"] == "v1"  # 回滚后旧服务仍可用
        # 修正代码后观察器继续工作。
        plugin_file.write_text(
            "from wb_data import VALUE\n\ndef plugin(ctx, config):\n    ctx.provide('value', VALUE)\n",
            encoding="utf-8",
        )
        data_file.write_text("VALUE = 'v3'\n", encoding="utf-8")
        watcher.notify("changed", str(data_file))
        await _wait_for(lambda: root.services.get("value") == "v3")
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_watcher_stop_ignores_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_file = tmp_path / "wb_data.py"
    plugin_file = tmp_path / "wb_plugin.py"
    _write_fixture(tmp_path, data_file, plugin_file, "v1")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "wb_counts", "wb_data", "wb_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "wb", "url": "wb_plugin:plugin", "config": {}}])

    backend = _FakeBackend()
    hmr = HMR(loader)
    watcher = hmr.watch([str(tmp_path)], backend=backend)
    await watcher.stop()
    assert not watcher.running and backend.stopped
    data_file.write_text("VALUE = 'v2'\n", encoding="utf-8")
    watcher.notify("changed", str(data_file))
    await asyncio.sleep(0.25)
    assert root.services["value"] == "v1"
    assert sys.modules["wb_counts"].A == 1
    hmr.dispose()
    await loader.dispose()
    await root.fiber.dispose()


def test_watch_requires_running_loop() -> None:
    root = Context()
    loader = Loader(root)
    hmr = HMR(loader)
    try:
        with pytest.raises(Exception, match="文件监听器"):
            hmr.watch(["."])
    finally:
        hmr.dispose()


@pytest.mark.asyncio
async def test_watchdog_backend_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("watchdog")
    data_file = tmp_path / "wb_data.py"
    plugin_file = tmp_path / "wb_plugin.py"
    _write_fixture(tmp_path, data_file, plugin_file, "v1")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "wb_counts", "wb_data", "wb_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "wb", "url": "wb_plugin:plugin", "config": {}}])

    hmr = HMR(loader)
    watcher = HMRWatcher(hmr, roots=[str(tmp_path)], debounce=0.05)
    watcher.start()
    try:
        data_file.write_text("VALUE = 'v2'\n", encoding="utf-8")
        # 真实文件系统事件有一定延迟：轮询等待。
        await _wait_for(lambda: root.services.get("value") == "v2", timeout=6.0)
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()
