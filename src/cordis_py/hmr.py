"""Basic hot module replacement helpers for development."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from typing import Any

from .loader import Loader, import_string


def module_name_from_url(url: str) -> str:
    """Extract the importable module name from a ``module:attr`` URL."""
    if ":" in url:
        return url.split(":", 1)[0]
    # Support ``package.module.attr`` as a fallback.
    return url.rpartition(".")[0]


def _remove_cached_pyc(module: Any) -> None:
    """Remove a module's bytecode cache file to force recompilation."""
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
    """Reload the module behind a plugin URL and return the new attribute."""
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
    """Development-oriented hot reload for loader-managed entries.

    This is intentionally minimal: it reloads one entry at a time and does not
    perform full dependency-graph classification. It is best used for plugins
    with few internal dependencies during development.
    """

    def __init__(self, loader: Loader) -> None:
        self.loader = loader

    async def reload_entry(self, entry_id: str) -> None:
        """Dispose and reload a single loader entry."""
        entry = self.loader.entries.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        fiber = self.loader.fibers.pop(entry_id, None)
        if fiber is not None:
            await fiber.dispose()
        # Re-import the module so the new code is used.
        reload_module(entry.url)
        await self.loader.enable(entry_id)

    async def reload_all(self) -> None:
        """Dispose and reload every loader-managed entry."""
        for entry_id in list(self.loader.entries):
            await self.reload_entry(entry_id)


__all__ = ["HMR", "reload_module"]
