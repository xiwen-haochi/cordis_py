"""通过 Python 包入口点发现插件。"""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .loader import Loader


def iter_entry_points(group: str = "cordis.plugins") -> list[EntryPoint]:
    """返回指定 *group* 下注册的所有入口点。"""
    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=group))
    return list(eps.get(group, []))


def discover(group: str = "cordis.plugins") -> list[dict[str, Any]]:
    """返回可直接交给 Loader 的插件条目字典列表。"""
    config: list[dict[str, Any]] = []
    for ep in iter_entry_points(group):
        config.append(
            {
                "id": ep.name,
                "url": ep.value,
                "config": {},
            }
        )
    return config


async def load_entry_points(loader: Loader, group: str = "cordis.plugins") -> None:
    """发现入口点并协调到 Loader 中。"""
    await loader.reconcile(discover(group))


__all__ = ["discover", "iter_entry_points", "load_entry_points"]
