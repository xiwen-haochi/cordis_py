"""Cordis Python 运行时使用的异常。"""

from __future__ import annotations


class CordisError(Exception):
    """带有稳定机器可读错误码的基础异常。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class InactiveAccess(CordisError):
    """当插件访问已声明但尚未激活的服务时抛出。"""

    def __init__(self, name: str) -> None:
        super().__init__("INACTIVE_ACCESS", f"cannot get required service {name!r} in inactive context")


class UndeclaredAccess(CordisError):
    """当插件访问从未声明的服务时抛出。"""

    def __init__(self, name: str) -> None:
        super().__init__("UNDECLARED_ACCESS", f"cannot get property {name!r} without inject")


class ServiceConflict(CordisError):
    """当同一作用域内服务被重复提供时抛出。"""

    def __init__(self, name: str, owner: str) -> None:
        super().__init__("SERVICE_CONFLICT", f"service {name!r} has been registered by {owner!r}")


class InvalidEffect(CordisError):
    """当效果回调返回不支持的值时抛出。"""

    def __init__(self, value: object = None) -> None:
        super().__init__("INVALID_EFFECT", f"invalid effect value: {value!r}")


class InvalidPlugin(CordisError):
    """当插件不是受支持的形态时抛出。"""

    def __init__(self, plugin: object) -> None:
        super().__init__("INVALID_PLUGIN", f"invalid plugin: {plugin!r}")


class InactiveEffect(CordisError):
    """当 fiber 已卸载后仍注册效果时抛出。"""

    def __init__(self) -> None:
        super().__init__("INACTIVE_EFFECT", "cannot register an effect on an inactive fiber")


class AsyncRequiredError(CordisError):
    """同步模式下遇到需要事件循环的异步操作时抛出。

    这是同步/异步双模式封装的边界错误：在没有运行事件循环的调用链中，
    如果插件、效果或事件监听器需要事件循环服务（如 asyncio.sleep、
    asyncio.create_task），同步驱动无法完成，会抛出该异常提示改用异步 API。
    """

    def __init__(self, where: str = "当前操作") -> None:
        super().__init__(
            "ASYNC_REQUIRED",
            f"{where} requires an event loop; please run it under asyncio or use the async API",
        )


class InvalidRequirement(CordisError):
    """当插件声明的服务约束无效时抛出。

    约束声明是声明期元数据：服务名未在 ``inject`` 中声明、PEP 440 specifier
    语法错误、或约束形态不受支持，都在插件注册时立即报错。
    """

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(
            "INVALID_REQUIREMENT",
            f"invalid requirement for {name!r}: {reason}",
        )


class ConfigValidationError(CordisError):
    """当插件配置未通过 ``Config`` 校验器时抛出。

    携带插件名与校验器的原始错误消息，便于定位是哪个插件、哪条配置失败。
    """

    def __init__(self, plugin: str, reason: str) -> None:
        super().__init__(
            "CONFIG_VALIDATION",
            f"invalid config for plugin {plugin!r}: {reason}",
        )
