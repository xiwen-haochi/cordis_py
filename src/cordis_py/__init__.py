"""Cordis Python：面向动态系统的时空可组合性。"""

from __future__ import annotations

from .context import Context
from .depgraph import Classification, ModuleGraph
from .discovery import discover, load_entry_points
from .errors import (
    AsyncRequiredError,
    ConfigValidationError,
    CordisError,
    InactiveAccess,
    InactiveEffect,
    InvalidEffect,
    InvalidPlugin,
    InvalidRequirement,
    ServiceConflict,
    UndeclaredAccess,
)
from .events import DispatchMode, EventsService
from .fiber import Fiber, FiberState
from .hmr import HMR
from .loader import Entry, Loader
from .registry import RegistryService
from .service import Service, inject, require
from .utils import Disposable, Effect, Inject, deep_merge

__all__ = [
    "HMR",
    "AsyncRequiredError",
    "Classification",
    "ConfigValidationError",
    "Context",
    "CordisError",
    "DispatchMode",
    "Disposable",
    "Effect",
    "Entry",
    "EventsService",
    "Fiber",
    "FiberState",
    "InactiveAccess",
    "InactiveEffect",
    "Inject",
    "InvalidEffect",
    "InvalidPlugin",
    "InvalidRequirement",
    "Loader",
    "ModuleGraph",
    "RegistryService",
    "Service",
    "ServiceConflict",
    "UndeclaredAccess",
    "deep_merge",
    "discover",
    "inject",
    "load_entry_points",
    "require",
]

__version__ = "0.4.0"
