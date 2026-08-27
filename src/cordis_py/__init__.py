"""Cordis Python：面向动态系统的时空可组合性。"""

from __future__ import annotations

from .context import Context
from .discovery import discover, load_entry_points
from .errors import (
    CordisError,
    InactiveAccess,
    InactiveEffect,
    InvalidEffect,
    InvalidPlugin,
    ServiceConflict,
    UndeclaredAccess,
)
from .events import DispatchMode, EventsService
from .fiber import Fiber, FiberState
from .hmr import HMR
from .loader import Entry, Loader
from .registry import RegistryService
from .service import Service, inject
from .utils import Disposable, Effect, Inject

__all__ = [
    "HMR",
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
    "Loader",
    "RegistryService",
    "Service",
    "ServiceConflict",
    "UndeclaredAccess",
    "discover",
    "inject",
    "load_entry_points",
]

__version__ = "0.1.0"
