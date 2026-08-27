from __future__ import annotations

from pathlib import Path

import pytest

from cordis_py import Context, Loader


@pytest.fixture
def plugin_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    module = tmp_path / "sample_plugins.py"
    module.write_text(
        """
from cordis_py import Context

def greeter(ctx: Context, config: dict):
    name = config.get("name", "world")
    ctx.provide("greeter", {"name": name})
    return lambda: print("greeter stopped")

def consumer(ctx: Context, config: dict):
    print("consumer sees", ctx.greeter)
    return lambda: print("consumer stopped")

consumer.inject = ["greeter"]
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return "sample_plugins"


async def test_loader_reconcile_and_disable(plugin_module: str) -> None:
    root = Context()
    loader = Loader(root)
    await loader.reconcile(
        [
            {"id": "greeter", "url": f"{plugin_module}:greeter", "config": {"name": "py"}},
            {"id": "consumer", "url": f"{plugin_module}:consumer", "config": {}},
        ]
    )
    assert set(loader.fibers) == {"greeter", "consumer"}
    assert root.services["greeter"] == {"name": "py"}

    await loader.disable("greeter")
    assert "greeter" not in loader.fibers
    assert "greeter" not in root.services

    await loader.enable("greeter")
    assert "greeter" in loader.fibers
    assert root.services["greeter"] == {"name": "py"}

    await loader.dispose()
    await root.fiber.dispose()


async def test_loader_remove_on_reconcile(plugin_module: str) -> None:
    root = Context()
    loader = Loader(root)
    await loader.reconcile(
        [
            {"id": "greeter", "url": f"{plugin_module}:greeter", "config": {}},
            {"id": "consumer", "url": f"{plugin_module}:consumer", "config": {}},
        ]
    )
    assert "consumer" in loader.fibers
    await loader.reconcile(
        [
            {"id": "greeter", "url": f"{plugin_module}:greeter", "config": {}},
        ]
    )
    assert "consumer" not in loader.fibers
    await loader.dispose()
    await root.fiber.dispose()
