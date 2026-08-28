from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from cordis_py import HMR, Context, Loader, ModuleGraph


def _unload(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """从 sys.modules 中卸载指定模块，避免跨测试串扰。"""
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)


async def test_hmr_reload_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = tmp_path / "hot_plugin.py"
    module.write_text(
        """
from cordis_py import Context

def plugin(ctx: Context, config: dict):
    ctx.provide("value", "v1")
    return None
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "hot_plugin")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "hot", "url": "hot_plugin:plugin", "config": {}}])
    assert root.services["value"] == "v1"

    module.write_text(
        """
from cordis_py import Context

def plugin(ctx: Context, config: dict):
    ctx.provide("value", "v2")
    return None
""",
        encoding="utf-8",
    )

    hmr = HMR(loader)
    try:
        assert await hmr.reload_entry("hot") == ["hot"]
        assert root.services["value"] == "v2"
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_graph_tracks_runtime_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "dep_a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "dep_b.py").write_text("B = 2\n", encoding="utf-8")
    (tmp_path / "dep_c.py").write_text("C = 3\n", encoding="utf-8")
    (tmp_path / "owner.py").write_text(
        "import os\nimport dep_a\nfrom dep_b import B\n\ndef load_dynamic():\n    import dep_c\n    return dep_c\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "owner", "dep_a", "dep_b", "dep_c")

    graph = ModuleGraph()
    graph.install()
    try:
        owner = importlib.import_module("owner")
        # 函数体内的延迟导入在调用时才执行，边在那一刻记录。
        owner.load_dynamic()
        assert {"dep_a", "dep_b", "dep_c"} <= graph.imports("owner")
        # 标准库导入不产生边。
        assert "os" not in graph.imports("owner")
        # 卸载追踪器后不再记录新边。
        graph.uninstall()
        (tmp_path / "dep_d.py").write_text("D = 4\n", encoding="utf-8")
        importlib.import_module("dep_d")
        assert "dep_d" not in graph.imports("owner")
    finally:
        graph.uninstall()


async def test_graph_completes_loaded_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pre_dep.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "pre_helper.py").write_text("import pre_dep\n", encoding="utf-8")
    pkg = tmp_path / "pkgx"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sibling.py").write_text("S = 10\n", encoding="utf-8")
    (pkg / "modx.py").write_text("from .sibling import S\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "pre_helper", "pre_dep", "pkgx", "pkgx.sibling", "pkgx.modx")

    # HMR 创建前已加载的模块：边应来自 AST 静态补全。
    importlib.import_module("pre_helper")
    importlib.import_module("pkgx.modx")

    graph = ModuleGraph()
    graph.install()
    try:
        assert "pre_dep" in graph.imports("pre_helper")
        assert "pkgx.sibling" in graph.imports("pkgx.modx")
    finally:
        graph.uninstall()


async def test_classify_accepted_and_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "leaf.py").write_text("L = 1\n", encoding="utf-8")
    (tmp_path / "mid.py").write_text("import leaf\n", encoding="utf-8")
    (tmp_path / "top.py").write_text("import mid\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("top", "mid", "leaf"):
        _unload(monkeypatch, name)
    importlib.import_module("top")

    graph = ModuleGraph()
    graph.install()
    try:
        # 变更在顶层：其依赖子树（未触达变更）应全部 declined。
        result = graph.classify({"top"})
        assert result.accepted == frozenset({"top"})
        assert result.declined == frozenset({"mid", "leaf"})
        # 变更仅叶子自身：只有自身 accepted（importer 侧由条目轮处理）。
        result2 = graph.classify({"leaf"})
        assert result2.accepted == frozenset({"leaf"})
        # 空变更：空分类。
        result3 = graph.classify(set())
        assert result3.accepted == frozenset() and result3.declined == frozenset()
    finally:
        graph.uninstall()


async def test_reload_file_reloads_shared_dependents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = tmp_path / "hmr_helper.py"
    helper.write_text("VALUE = 'v1'\n", encoding="utf-8")
    (tmp_path / "hmr_counts.py").write_text("A = 0\nB = 0\nC = 0\n", encoding="utf-8")
    (tmp_path / "hmr_a.py").write_text(
        "import hmr_counts\nfrom hmr_helper import VALUE\n\nhmr_counts.A += 1\n\n"
        "def plugin(ctx, config):\n    ctx.provide('value_a', VALUE)\n",
        encoding="utf-8",
    )
    (tmp_path / "hmr_b.py").write_text(
        "import hmr_counts\nfrom hmr_helper import VALUE\n\nhmr_counts.B += 1\n\n"
        "def plugin(ctx, config):\n    ctx.provide('value_b', VALUE)\n",
        encoding="utf-8",
    )
    (tmp_path / "hmr_c.py").write_text(
        "import hmr_counts\n\nhmr_counts.C += 1\n\n"
        "def plugin(ctx, config):\n    ctx.provide('value_c', 'c1')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("hmr_helper", "hmr_counts", "hmr_a", "hmr_b", "hmr_c"):
        _unload(monkeypatch, name)

    root = Context()
    loader = Loader(root)
    await loader.reconcile(
        [
            {"id": "a", "url": "hmr_a:plugin", "config": {}},
            {"id": "b", "url": "hmr_b:plugin", "config": {}},
            {"id": "c", "url": "hmr_c:plugin", "config": {}},
        ]
    )
    assert root.services["value_a"] == "v1"
    assert root.services["value_b"] == "v1"
    assert root.services["value_c"] == "c1"

    hmr = HMR(loader)
    try:
        assert hmr.affected({"hmr_helper"}) == ["a", "b"]
        helper.write_text("VALUE = 'v2'\n", encoding="utf-8")
        assert await hmr.reload_file(helper) == ["a", "b"]
        assert root.services["value_a"] == "v2"
        assert root.services["value_b"] == "v2"
        assert root.services["value_c"] == "c1"
        # 无关条目未重新执行，相关条目均重执行了一次（计数器模块未被重载）。
        assert sys.modules["hmr_counts"].C == 1
        assert sys.modules["hmr_counts"].A == 2
        assert sys.modules["hmr_counts"].B == 2
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_reload_entry_reloads_dependents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ent_a.py").write_text(
        "VALUE_A = 'a1'\n\ndef plugin(ctx, config):\n    ctx.provide('value_a', VALUE_A)\n",
        encoding="utf-8",
    )
    (tmp_path / "ent_b.py").write_text(
        "import ent_a\n\ndef plugin(ctx, config):\n    ctx.provide('value_b', ent_a.VALUE_A)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("ent_a", "ent_b"):
        _unload(monkeypatch, name)

    root = Context()
    loader = Loader(root)
    await loader.reconcile(
        [
            {"id": "a", "url": "ent_a:plugin", "config": {}},
            {"id": "b", "url": "ent_b:plugin", "config": {}},
        ]
    )
    assert root.services["value_a"] == "a1"
    assert root.services["value_b"] == "a1"

    hmr = HMR(loader)
    try:
        (tmp_path / "ent_a.py").write_text(
            "VALUE_A = 'a2'\n\ndef plugin(ctx, config):\n    ctx.provide('value_a', VALUE_A)\n",
            encoding="utf-8",
        )
        assert await hmr.reload_entry("a") == ["a", "b"]
        assert root.services["value_a"] == "a2"
        assert root.services["value_b"] == "a2"
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_rollback_on_apply_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_file = tmp_path / "hmr_bad.py"
    plugin_file.write_text(
        "def plugin(ctx, config):\n    ctx.provide('value', 'v1')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "hmr_bad")

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "bad", "url": "hmr_bad:plugin", "config": {}}])
    assert root.services["value"] == "v1"

    hmr = HMR(loader)
    try:
        plugin_file.write_text(
            "def plugin(ctx, config):\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="boom"):
            await hmr.reload_file(plugin_file)
        # 回滚后旧服务仍然可用。
        assert root.services["value"] == "v1"
        assert "bad" in loader.fibers
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_reload_file_unknown_is_noop(tmp_path: Path) -> None:
    root = Context()
    loader = Loader(root)
    hmr = HMR(loader)
    try:
        assert await hmr.reload_file(tmp_path / "never_imported.py") == []
        assert await hmr.reload_file(tmp_path / "notes.md") == []
        with pytest.raises(KeyError):
            await hmr.reload_entry("missing")
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_reload_module_imports_freshly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "lone_mod.py").write_text("LONE = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    _unload(monkeypatch, "lone_mod")

    root = Context()
    loader = Loader(root)
    hmr = HMR(loader)
    try:
        assert await hmr.reload_module("lone_mod") == []
        assert "lone_mod" in sys.modules
    finally:
        hmr.dispose()
        await loader.dispose()
        await root.fiber.dispose()


async def test_dispose_uninstalls_tracer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "post_owner.py").write_text("import post_dep\n", encoding="utf-8")
    (tmp_path / "post_dep.py").write_text("P = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("post_owner", "post_dep"):
        _unload(monkeypatch, name)

    root = Context()
    loader = Loader(root)
    hmr = HMR(loader)
    assert hmr.graph.installed
    hmr.dispose()
    assert not hmr.graph.installed
    try:
        importlib.import_module("post_owner")
        assert "post_dep" not in hmr.graph.imports("post_owner")
    finally:
        await loader.dispose()
        await root.fiber.dispose()
