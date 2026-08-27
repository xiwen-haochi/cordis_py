"""Cordis Python 声明式组件加载器。"""

from __future__ import annotations

import importlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import Context
from .fiber import Fiber


def import_string(path: str) -> Any:
    """导入 ``module:attr`` 或 ``module.attr`` 并返回对应属性。"""
    if ":" in path:
        module_name, attr = path.split(":", 1)
    else:
        module_name, _, attr = path.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


@dataclass
class Entry:
    """待加载 fiber 的声明式描述。"""

    id: str
    url: str
    config: dict[str, Any] = field(default_factory=dict)
    disabled: bool = False
    isolate: dict[str, Any] = field(default_factory=dict)
    intercept: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        return cls(
            id=str(data["id"]),
            url=str(data["url"]),
            config=dict(data.get("config") or {}),
            disabled=bool(data.get("disabled", False)),
            isolate=dict(data.get("isolate") or {}),
            intercept=dict(data.get("intercept") or {}),
        )


class Loader:
    """从声明式配置加载并协调插件。

    Loader 接受 :class:`Entry` 形状的字典列表，或
    ``{"plugins": [...]}`` 文档。它会惰性创建 fiber，并可通过 :meth:`reconcile` 增量更新。
    """

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.entries: dict[str, Entry] = {}
        self.fibers: dict[str, Fiber] = {}

    def _normalize_config(self, config: Any) -> list[dict[str, Any]]:
        if isinstance(config, list):
            return list(config)
        if isinstance(config, dict) and "plugins" in config:
            return list(config["plugins"])
        raise TypeError("loader config must be a list of entries or a document with a 'plugins' list")

    @staticmethod
    def _load_file(path: str | Path) -> Any:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        if suffix in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise ImportError("PyYAML is required to load YAML files") from exc
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        if suffix == ".toml":
            return tomllib.loads(path.read_text(encoding="utf-8"))
        raise ValueError(f"unsupported config file suffix: {suffix}")

    async def include(self, path: str | Path) -> None:
        """加载配置文件并将其设为期望状态。"""
        config = self._load_file(path)
        await self.reconcile(config)

    async def reconcile(self, config: Any) -> None:
        """让运行中的 fiber 树与给定声明式配置保持一致。"""
        raw_entries = self._normalize_config(config)
        desired: dict[str, Entry] = {}
        for raw in raw_entries:
            entry = Entry.from_dict(raw)
            desired[entry.id] = entry

        # 移除已消失的条目。
        for entry_id, fiber in list(self.fibers.items()):
            if entry_id not in desired:
                await fiber.dispose()
                self.fibers.pop(entry_id, None)
                self.entries.pop(entry_id, None)

        # 新增或更新条目。
        for entry_id, entry in desired.items():
            old = self.entries.get(entry_id)
            if old is None:
                self.entries[entry_id] = entry
                if not entry.disabled:
                    await self._start_entry(entry)
            elif old.url != entry.url or old.disabled != entry.disabled:
                self.entries[entry_id] = entry
                existing = self.fibers.get(entry_id)
                if existing is not None:
                    await existing.dispose()
                    self.fibers.pop(entry_id, None)
                if not entry.disabled:
                    await self._start_entry(entry)
            elif old.config != entry.config:
                self.entries[entry_id] = entry
                existing = self.fibers.get(entry_id)
                if existing is not None and not entry.disabled:
                    await existing.update(entry.config)
                elif existing is None and not entry.disabled:
                    await self._start_entry(entry)

    async def _start_entry(self, entry: Entry) -> Fiber:
        plugin = import_string(entry.url)
        context = self.ctx
        # 存在 isolate/intercept 时，通过子上下文应用简单隔离/拦截。
        for name, realm in entry.isolate.items():
            context = context.isolate(name, realm)
        for name, metadata in entry.intercept.items():
            context = context.intercept(name, metadata)
        fiber = context.plugin(plugin, entry.config)
        await fiber
        self.fibers[entry.id] = fiber
        return fiber

    async def disable(self, entry_id: str) -> None:
        """禁用条目并卸载其 fiber。"""
        fiber = self.fibers.pop(entry_id, None)
        if fiber is not None:
            await fiber.dispose()
        if entry_id in self.entries:
            self.entries[entry_id].disabled = True

    async def enable(self, entry_id: str) -> None:
        """启用之前被禁用的条目。"""
        entry = self.entries.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        entry.disabled = False
        if entry_id not in self.fibers:
            await self._start_entry(entry)

    async def dispose(self) -> None:
        """卸载所有由 Loader 管理的 fiber。"""
        for fiber in reversed(list(self.fibers.values())):
            await fiber.dispose()
        self.fibers.clear()
        self.entries.clear()


__all__ = ["Entry", "Loader", "import_string"]
