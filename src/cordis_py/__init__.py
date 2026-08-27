"""Cordis Python: spatiotemporal composability for dynamic systems."""

from __future__ import annotations

from .context import Context
from .errors import (
    CordisError,
    InactiveAccess,
    InactiveEffect,
    InvalidEffect,
    InvalidPlugin,
    ServiceConflict,
    UndeclaredAccess,
)
from .fiber import Fiber, FiberState
from .service import Service, inject
from .utils import Disposable, Effect, Inject

__all__ = [
    "Context",
    "CordisError",
    "Disposable",
    "Effect",
    "Fiber",
    "FiberState",
    "InactiveAccess",
    "InactiveEffect",
    "Inject",
    "InvalidEffect",
    "InvalidPlugin",
    "Service",
    "ServiceConflict",
    "UndeclaredAccess",
    "inject",
]

__version__ = "0.1.0"
