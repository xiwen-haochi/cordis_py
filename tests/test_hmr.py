from __future__ import annotations

from pathlib import Path

import pytest

from cordis_py import HMR, Context, Loader


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
    await hmr.reload_entry("hot")
    assert root.services["value"] == "v2"

    await loader.dispose()
    await root.fiber.dispose()
