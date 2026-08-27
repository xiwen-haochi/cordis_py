"""Plugin discovery through Python package entry points."""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .loader import Loader


def iter_entry_points(group: str = "cordis.plugins") -> list[EntryPoint]:
    """Return all entry points registered under *group*."""
    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=group))
    return list(eps.get(group, []))


def discover(group: str = "cordis.plugins") -> list[dict[str, Any]]:
    """Return loader-ready entry dicts for discovered plugins."""
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
    """Discover entry points and reconcile them into a loader."""
    await loader.reconcile(discover(group))


__all__ = ["discover", "iter_entry_points", "load_entry_points"]
