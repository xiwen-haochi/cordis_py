"""Errors used by the Cordis Python runtime."""

from __future__ import annotations


class CordisError(Exception):
    """Base error with a stable machine-readable code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class InactiveAccess(CordisError):
    """Raised when a plugin accesses a declared but not-yet-active service."""

    def __init__(self, name: str) -> None:
        super().__init__("INACTIVE_ACCESS", f"cannot get required service {name!r} in inactive context")


class UndeclaredAccess(CordisError):
    """Raised when a plugin accesses a service that was never declared."""

    def __init__(self, name: str) -> None:
        super().__init__("UNDECLARED_ACCESS", f"cannot get property {name!r} without inject")


class ServiceConflict(CordisError):
    """Raised when a service is provided more than once in the same scope."""

    def __init__(self, name: str, owner: str) -> None:
        super().__init__("SERVICE_CONFLICT", f"service {name!r} has been registered by {owner!r}")


class InvalidEffect(CordisError):
    """Raised when an effect callback returns an unsupported value."""

    def __init__(self, value: object = None) -> None:
        super().__init__("INVALID_EFFECT", f"invalid effect value: {value!r}")


class InvalidPlugin(CordisError):
    """Raised when a plugin is not a supported shape."""

    def __init__(self, plugin: object) -> None:
        super().__init__("INVALID_PLUGIN", f"invalid plugin: {plugin!r}")


class InactiveEffect(CordisError):
    """Raised when an effect is registered after the fiber was disposed."""

    def __init__(self) -> None:
        super().__init__("INACTIVE_EFFECT", "cannot register an effect on an inactive fiber")
