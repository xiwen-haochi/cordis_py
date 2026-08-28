"""开发期模块依赖图：追踪导入边并提供 HMR 分类。"""

from __future__ import annotations

import ast
import os
import site
import sys
import sysconfig
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

# 栈帧检查时需要跳过的导入引导器内部模块。
_TRANSPARENT_PREFIXES = ("importlib",)

__all__ = ["Classification", "ModuleGraph"]


def _module_file(module: ModuleType) -> str | None:
    """返回模块源码文件的绝对路径，无法确定时返回 None。"""
    file = getattr(module, "__file__", None)
    if file:
        return os.path.realpath(file)
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None) if spec is not None else None
    if isinstance(origin, str) and origin not in ("built-in", "frozen"):
        return os.path.realpath(origin)
    return None


def _under(path: str, bases: Sequence[str]) -> bool:
    """判断路径是否位于任一基础目录之下。"""
    for base in bases:
        if path == base or path.startswith(base + os.sep):
            return True
    return False


@dataclass(frozen=True)
class Classification:
    """HMR 分类结果：需重载的模块与无需重载的模块。"""

    accepted: frozenset[str]
    declined: frozenset[str]


class ModuleGraph:
    """记录模块导入边并提供 accepted/declined 分类。

    模块图由两路数据构成：

    - **运行时追踪器**：安装于 ``sys.meta_path`` 的前缀 finder，在每次模块导入时
      记录“哪个模块导入了哪个模块”。精确覆盖 ``import``、``from-import`` 与函数
      体内的延迟导入。
    - **AST 静态补全**：安装时对 ``sys.modules`` 中已加载的纯 Python 模块解析
      源码，补全追踪器安装前就已经发生的导入。
    """

    def __init__(self, *, exclude_prefixes: Sequence[str] = ("cordis_py",)) -> None:
        self._exclude_prefixes = tuple(exclude_prefixes)
        self._edges: dict[str, set[str]] = {}
        self._finder: _ImportTraceFinder | None = None
        self._stdlib_names = frozenset(getattr(sys, "stdlib_module_names", ()))
        self._base_dirs = self._compute_base_dirs()

    @staticmethod
    def _compute_base_dirs() -> tuple[str, ...]:
        """计算 stdlib 与 site-packages 的目录前缀，用于 external 判定。"""
        dirs: list[str] = []
        all_paths = sysconfig.get_paths()
        for key in ("stdlib", "purelib", "platlib"):
            path = all_paths.get(key)
            if path:
                dirs.append(os.path.realpath(path))
        try:
            dirs.extend(os.path.realpath(p) for p in site.getsitepackages())
        except Exception:  # noqa: BLE001, S110 - 某些平台没有 site-packages 概念
            pass
        try:
            user = site.getusersitepackages()
            if user:
                dirs.append(os.path.realpath(user))
        except Exception:  # noqa: BLE001, S110
            pass
        return tuple(dirs)

    @property
    def installed(self) -> bool:
        """追踪器是否已安装。"""
        return self._finder is not None

    def install(self) -> None:
        """安装追踪器并对已加载模块做 AST 补全（幂等）。"""
        if self._finder is None:
            self._finder = _ImportTraceFinder(self)
            sys.meta_path.insert(0, self._finder)
            self._complete_loaded_modules()

    def uninstall(self) -> None:
        """移除追踪器，停止记录新的导入边（幂等）。"""
        if self._finder is not None:
            try:
                sys.meta_path.remove(self._finder)
            except ValueError:
                pass
            self._finder = None

    # ------------------------------------------------------------------
    # 边记录
    # ------------------------------------------------------------------

    def _trace_import(self, fullname: str) -> None:
        """记录一次运行时导入边（由追踪器回调）。"""
        if self._external_by_name(fullname):
            return
        importer = self._find_importer()
        if importer is None or importer == fullname:
            return
        if self.is_external(importer):
            return
        self._edges.setdefault(importer, set()).add(fullname)

    @staticmethod
    def _find_importer() -> str | None:
        """向上查找第一个在 ``sys.modules`` 中的非引导器栈帧作为导入者。"""
        # _getframe(3)：_find_importer <- _trace_import <- find_spec <- 导入引导器。
        frame = sys._getframe(3)
        depth = 0
        while frame is not None and depth < 64:
            name = frame.f_globals.get("__name__")
            if isinstance(name, str) and name in sys.modules:
                if name.startswith(_TRANSPARENT_PREFIXES):
                    frame = frame.f_back
                    depth += 1
                    continue
                return name
            frame = frame.f_back
            depth += 1
        return None

    def _complete_loaded_modules(self) -> None:
        """对已加载的纯 Python 模块做 AST 静态补全。"""
        for module in list(sys.modules.values()):
            if not isinstance(module, ModuleType):
                continue
            name = getattr(module, "__name__", "")
            if not name or self.is_external(name):
                continue
            file = _module_file(module)
            if not file or not file.endswith(".py"):
                continue
            for target in _static_imports(module, file):
                self._edges.setdefault(name, set()).add(target)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def imports(self, name: str) -> frozenset[str]:
        """模块的直接导入（前向依赖边）。"""
        return frozenset(self._edges.get(name, ()))

    def closure(self, name: str, *, skip: set[str] | frozenset[str] | None = None) -> set[str]:
        """返回模块的传递依赖闭包（含自身），剪枝外部模块与 skip 集合。"""
        skip = skip or set()
        result: set[str] = set()
        stack = [name]
        while stack:
            current = stack.pop()
            if current in result or current in skip or self.is_external(current):
                continue
            result.add(current)
            stack.extend(self._edges.get(current, ()))
        return result

    def module_to_file(self, name: str) -> str | None:
        """返回已加载模块对应的源码文件路径。"""
        module = sys.modules.get(name)
        if isinstance(module, ModuleType):
            return _module_file(module)
        return None

    def file_to_module(self, path: str) -> str | None:
        """把文件路径解析为已加载模块名；未加载或非源码文件返回 None。"""
        target = os.path.realpath(os.path.expanduser(str(path)))
        for name, module in list(sys.modules.items()):
            if isinstance(module, ModuleType) and _module_file(module) == target:
                return name
        return None

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------

    def classify(self, changed: Iterable[str]) -> Classification:
        """对变更模块集合做 accepted/declined 分类。

        与 Node Cordis v4 的 ``analyzeChanges`` 对齐：

        - 节点 accepted，当且仅当其任一（直接或传递）依赖被 accepted；
        - 节点 declined，当且仅当其全部依赖均 declined 或为外部模块；
        - 依赖状态未定者延迟到下一轮；迭代无进展后残余一律 declined。
        """
        changed_set = set(changed)
        accepted: set[str] = set(changed_set)
        declined: set[str] = set()
        pending: list[str] = []

        for url in changed_set:
            for child in self.imports(url):
                if child in accepted or child in declined or self.is_external(child):
                    continue
                if child not in pending:
                    pending.append(child)

        while True:
            index = 0
            has_update = False
            while index < len(pending):
                url = pending[index]
                children = self.imports(url)
                is_declined = True
                is_accepted = False
                for child in children:
                    if child in declined or self.is_external(child):
                        continue
                    if child in accepted:
                        is_accepted = True
                        break
                    is_declined = False
                    if (
                        child not in pending
                        and child not in accepted
                        and child not in declined
                        and not self.is_external(child)
                    ):
                        has_update = True
                        pending.append(child)
                if is_accepted or is_declined:
                    has_update = True
                    pending.pop(index)
                    (accepted if is_accepted else declined).add(url)
                else:
                    index += 1
            if not has_update:
                break

        for url in pending:
            declined.add(url)
        return Classification(frozenset(accepted), frozenset(declined))

    # ------------------------------------------------------------------
    # external 判定
    # ------------------------------------------------------------------

    def _external_by_name(self, name: str) -> bool:
        """仅按名字判定的外部模块检查（不访问 sys.modules）。"""
        if name == "__main__" or name in self._stdlib_names:
            return True
        return name.startswith(self._exclude_prefixes)

    def is_external(self, name: str) -> bool:
        """外部模块：stdlib / site-packages / 排除前缀 / ``__main__``。"""
        if self._external_by_name(name):
            return True
        module = sys.modules.get(name)
        if isinstance(module, ModuleType):
            file = _module_file(module)
            if file and _under(file, self._base_dirs):
                return True
        return False


class _ImportTraceFinder:
    """``sys.meta_path`` 前缀 finder：只记录导入边，不参与实际加载。"""

    def __init__(self, graph: ModuleGraph) -> None:
        self._graph = graph

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        # 由调用方约定：返回 None 表示不参与加载，继续交给后续 finder。
        self._graph._trace_import(fullname)


def _static_imports(module: ModuleType, file: str) -> list[str]:
    """解析模块源码中的静态 import 边，返回目标模块名列表。"""
    try:
        tree = ast.parse(Path(file).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        # 源码缺失或暂不合法时跳过，由重载阶段给出真正的报错。
        return []
    package = getattr(module, "__package__", None) or ""
    targets: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
                top = alias.name.split(".", 1)[0]
                if top != alias.name:
                    targets.append(top)
        elif isinstance(node, ast.ImportFrom):
            full = _resolve_import_from(node, package)
            if not full:
                continue
            targets.append(full)
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub = f"{full}.{alias.name}"
                if sub in sys.modules:
                    targets.append(sub)
    return targets


def _resolve_import_from(node: ast.ImportFrom, package: str) -> str | None:
    """把 ``from ... import ...`` 节点解析为完整模块名。"""
    if node.level == 0:
        return node.module or ""
    parts = package.split(".") if package else []
    drop = node.level - 1
    if drop > len(parts):
        return None
    base = ".".join(parts[: len(parts) - drop])
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base
