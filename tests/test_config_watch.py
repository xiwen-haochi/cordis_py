"""配置热更测试：HMR.reload_config / watch_config（与源码 HMR 并列的能力）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from cordis_py import HMR, ConfigWatcher, Context, Loader


class _FakeBackend:
    """可注入的最小观察后端（与 test_watcher 的协议一致）。"""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def _plugin_file(tmp_path: Path) -> Path:
    """生成一个把 config['x'] 作为服务值的插件（重载/更新时统计执行次数）。"""
    mod = tmp_path / "cw_plugin.py"
    mod.write_text(
        "import cw_counts\n\n"
        "def plugin(ctx, config):\n"
        "    cw_counts.A += 1\n"
        "    ctx.provide('cw_value', config.get('x'))\n",
        encoding="utf-8",
    )
    (tmp_path / "cw_counts.py").write_text("A = 0\n", encoding="utf-8")
    return mod


def _config_file(tmp_path: Path, x: int) -> Path:
    cf = tmp_path / "app.json"
    cf.write_text(json.dumps({"plugins": [{"id": "cw", "url": "cw_plugin:plugin", "config": {"x": x}}]}), encoding="utf-8")
    return cf


def _unload(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "等待超时"
        await asyncio.sleep(0.01)


async def test_reload_config_updates_changed_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reload_config：config 变化的条目执行 update（值更新、效果撤销重装）。"""
    _plugin_file(tmp_path)
    cf = _config_file(tmp_path, 1)
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "cw_counts", "cw_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile(json.loads(cf.read_text()))
    assert root.services["cw_value"] == 1

    hmr = HMR(loader)
    try:
        cf.write_text(json.dumps({"plugins": [{"id": "cw", "url": "cw_plugin:plugin", "config": {"x": 2}}]}), encoding="utf-8")
        affected = await hmr.reload_config(cf)
        assert affected == ["cw"]
        assert root.services["cw_value"] == 2
        # 条目被整体更新：插件重新执行了一次（旧效果撤销 + 新配置重启）。
        assert sys.modules["cw_counts"].A == 2
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_reload_config_no_change_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reload_config：config 未变时是空操作（增量协调，不重启任何条目）。"""
    _plugin_file(tmp_path)
    cf = _config_file(tmp_path, 1)
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "cw_counts", "cw_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile(json.loads(cf.read_text()))

    hmr = HMR(loader)
    try:
        affected = await hmr.reload_config(cf)
        assert affected == []
        assert sys.modules["cw_counts"].A == 1  # 没有重新执行
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_watch_config_reloads_on_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watch_config：文件变更自动触发 reload_config（真实 watchdog 后端）。"""
    _plugin_file(tmp_path)
    cf = _config_file(tmp_path, 1)
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "cw_counts", "cw_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile(json.loads(cf.read_text()))
    assert root.services["cw_value"] == 1

    hmr = HMR(loader)
    watcher = hmr.watch_config([cf])
    assert isinstance(watcher, ConfigWatcher) and watcher.running
    try:
        cf.write_text(json.dumps({"plugins": [{"id": "cw", "url": "cw_plugin:plugin", "config": {"x": 3}}]}), encoding="utf-8")
        await _wait_for(lambda: root.services.get("cw_value") == 3)
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_watch_config_ignores_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watch_config：只响应目标配置文件路径，其他文件（含目录内兄弟文件）被过滤。"""
    _plugin_file(tmp_path)
    cf = _config_file(tmp_path, 1)
    unrelated = tmp_path / "other.yml"
    unrelated.write_text("plugins: []\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "cw_counts", "cw_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile(json.loads(cf.read_text()))

    hmr = HMR(loader)
    watcher = hmr.watch_config([cf], debounce=0.02)
    try:
        watcher.notify("changed", str(unrelated))
        watcher.notify("deleted", str(cf))
        await asyncio.sleep(0.15)
        assert root.services["cw_value"] == 1
        assert sys.modules["cw_counts"].A == 1
        # 观察器仍存活：目标文件变更依旧生效。
        cf.write_text(json.dumps({"plugins": [{"id": "cw", "url": "cw_plugin:plugin", "config": {"x": 4}}]}), encoding="utf-8")
        watcher.notify("changed", str(cf))
        await _wait_for(lambda: root.services.get("cw_value") == 4)
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_watch_config_error_callback_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watch_config：非法配置导致 reload_config 抛错时走 on_error 且观察继续。"""
    _plugin_file(tmp_path)
    cf = _config_file(tmp_path, 1)
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "cw_counts", "cw_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile(json.loads(cf.read_text()))

    errors: list[str] = []
    hmr = HMR(loader)
    watcher = hmr.watch_config([cf], debounce=0.02, on_error=lambda p, e: errors.append(p))
    try:
        cf.write_text("{{ 不是合法 JSON }}", encoding="utf-8")
        await _wait_for(lambda: bool(errors))
        # 修复后恢复：观察持续进行。
        cf.write_text(json.dumps({"plugins": [{"id": "cw", "url": "cw_plugin:plugin", "config": {"x": 5}}]}), encoding="utf-8")
        await _wait_for(lambda: root.services.get("cw_value") == 5)
    finally:
        await watcher.stop()
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


def test_watch_config_requires_running_loop() -> None:
    """无事件循环的同步链路直接抛 AsyncRequiredError（与 watch 一致）。"""
    from cordis_py import AsyncRequiredError

    root = Context()
    loader = Loader(root)
    hmr = HMR(loader)
    try:
        with pytest.raises(AsyncRequiredError):
            hmr.watch_config([Path("app.yml")])
    finally:
        root.fiber.dispose_sync()


def test_watch_config_requires_targets() -> None:
    root = Context()
    loader = Loader(root)
    hmr = HMR(loader)
    try:
        with pytest.raises(ValueError):
            hmr.watch_config([])
    finally:
        root.fiber.dispose_sync()


async def test_watch_config_fake_backend_protocol(tmp_path: Path) -> None:
    """注入后端的最小协议：start/stop 被调用，running 状态正确。"""
    root = Context()
    loader = Loader(root)
    hmr = HMR(loader)
    backend = _FakeBackend()
    watcher = hmr.watch_config([tmp_path / "app.yml"], backend=backend)
    assert watcher.running and backend.started
    await watcher.stop()
    assert not watcher.running and backend.stopped
    hmr.dispose()
    await root.fiber.dispose()
