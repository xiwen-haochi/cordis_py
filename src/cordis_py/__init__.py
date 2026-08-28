"""Cordis Python：面向动态系统的时空可组合性。"""

from __future__ import annotations

from .bridge import Bridge, RemoteService
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
    ProtocolError,
    RemoteClosed,
    RemoteError,
    ServiceConflict,
    UndeclaredAccess,
)
from .events import DispatchMode, EventsService
from .fiber import Fiber, FiberState
from .hmr import HMR
from .loader import Entry, Loader
from .registry import RegistryService
from .scope import Scope, bind_scope_parent, create_scope, scope_of, scope_target
from .service import Service, inject, require
from .utils import Disposable, Effect, Inject, deep_merge
from .watcher import HMRWatcher

__all__ = [
    "HMR",
    "AsyncRequiredError",
    "Bridge",
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
    "HMRWatcher",
    "InactiveAccess",
    "InactiveEffect",
    "Inject",
    "InvalidEffect",
    "InvalidPlugin",
    "InvalidRequirement",
    "Loader",
    "ModuleGraph",
    "ProtocolError",
    "RegistryService",
    "RemoteClosed",
    "RemoteError",
    "RemoteService",
    "Scope",
    "Service",
    "ServiceConflict",
    "UndeclaredAccess",
    "bind_scope_parent",
    "create_scope",
    "deep_merge",
    "discover",
    "inject",
    "load_entry_points",
    "require",
    "scope_of",
    "scope_target",
]

__version__ = "0.8.0"
