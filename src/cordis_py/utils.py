"""Cordis Python 运行时共用工具。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Mapping
from typing import Any, TypeVar

from .errors import AsyncRequiredError, ConfigValidationError, InvalidRequirement

T = TypeVar("T")

Disposable = Callable[[], Any]
Effect = (
    Disposable
    | Iterable[Disposable]
    | AsyncIterable[Disposable]
    | None
)
Inject = list[str] | Mapping[str, Any] | None


def is_async_callable(obj: Any) -> bool:
    """判断 *obj* 是否为异步函数或具有异步 __call__ 的对象。"""
    if inspect.iscoroutinefunction(obj):
        return True
    if callable(obj):
        return inspect.iscoroutinefunction(obj.__call__)
    return False


def maybe_await(value: Any) -> Any:
    """如果 *value* 可等待则返回其自身，否则原样返回。"""
    if inspect.isawaitable(value):
        return value
    return value


async def await_maybe(value: Any) -> Any:
    """如果 *value* 可等待则等待它，否则原样返回。"""
    if inspect.isawaitable(value):
        return await value
    return value


def has_running_loop() -> bool:
    """当前线程是否运行着事件循环。

    这是同步/异步双模式的分流点：有运行中的事件循环时，生命周期转换
    与异步效果走 ``asyncio`` 后台调度；否则走内联同步驱动。
    """
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def drive_sync(awaitable: Awaitable[Any], where: str = "当前操作") -> None:
    """在没有运行事件循环的线程中内联驱动一个协程。

    同步模式的核心组件：被驱动对象必须是普通协程，且其内部等待链也必须
    是纯协程（例如插件回调、异步效果收集器）。一旦协程挂起在需要事件循环
    服务的等待点（如 ``asyncio.sleep``、``asyncio.create_task``），或者使用了
    事件循环原语而抛出不带运行循环的 ``RuntimeError``，都会抛出
    :class:`AsyncRequiredError`，提示调用方改用异步 API。
    """
    if not inspect.iscoroutine(awaitable):
        raise AsyncRequiredError(where)
    try:
        awaitable.send(None)
    except StopIteration:
        return
    except RuntimeError as exc:
        awaitable.close()
        if "no running event loop" in str(exc):
            raise AsyncRequiredError(where) from exc
        raise
    # 协程挂起：说明有等待点需要事件循环服务。
    awaitable.close()
    raise AsyncRequiredError(where)


def normalize_sync_error(error: BaseException, where: str) -> None:
    """把同步模式收集到的事件循环相关错误转为 AsyncRequiredError 并抛出。

    插件体内使用 ``asyncio.sleep`` 等原语时，异常发生在协程内部，会先被
    fiber 记录为加载错误；在同步入口重新抛出前，用本函数给出清晰的
    双模式边界提示。
    """
    if isinstance(error, RuntimeError) and "no running event loop" in str(error):
        raise AsyncRequiredError(where) from error
    raise error


def resolve_inject(plugin: Any) -> dict[str, Any]:
    """将插件的 ``inject`` 元数据规范化为“名称 -> 配置”映射。"""
    raw = getattr(plugin, "inject", None)
    if raw is None:
        return {}
    if isinstance(raw, list):
        return {name: None for name in raw}
    if isinstance(raw, Mapping):
        return dict(raw)
    raise TypeError(f"invalid inject declaration: {raw!r}")


def collect_disposers(effect: Effect) -> list[Disposable]:
    """从支持的效果返回值中收集 disposer（仅同步）。

    异步可迭代对象由 fiber/context 调用方处理，因为需要异步事件循环。
    """
    if effect is None:
        return []
    if callable(effect):
        return [effect]
    if isinstance(effect, Iterable) and not isinstance(effect, (str, bytes)):
        return [item for item in effect if callable(item)]
    return []


def merge_config(base: Any, override: Any) -> Any:
    """合并两层配置：*override* 优先。

    两侧都是映射时做浅合并，否则整体替换。``None`` 表示“无配置”，会被跳过；
    该语义用于实现 intercept 配置链（见 :meth:`Context.intercept_config`）。
    """
    if override is None:
        return base
    if base is None:
        return override
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        return {**base, **override}
    return override


def normalize_constraint(name: str, constraint: Any) -> Any:
    """规范化单个服务约束。

    ``str`` 按 PEP 440 解析为 :class:`packaging.specifiers.SpecifierSet`，
    ``callable`` 原样保留（接收服务对象，返回真值即满足）；其他形态抛出
    :class:`InvalidRequirement`。
    """
    if isinstance(constraint, str):
        from packaging.specifiers import InvalidSpecifier, SpecifierSet

        try:
            return SpecifierSet(constraint)
        except InvalidSpecifier as exc:
            raise InvalidRequirement(name, f"invalid specifier {constraint!r}: {exc}") from exc
    if callable(constraint):
        return constraint
    raise InvalidRequirement(name, f"unsupported constraint type: {type(constraint).__name__}")


def constraint_matches(constraint: Any, value: Any, version: str | None) -> tuple[bool, str | None]:
    """检查单个约束是否满足，返回 ``(满足与否, 原因)``。

    版本约束（:class:`SpecifierSet`）：提供方未声明版本或版本号非法视为不满足
    （保守语义，等提供方声明版本后自动激活）。
    接口谓词（callable）：谓词抛异常视为不满足，原因中记录异常消息。
    """
    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion

    if isinstance(constraint, SpecifierSet):
        if version is None:
            return False, "服务未声明版本"
        try:
            ok = constraint.contains(version)
        except InvalidVersion:
            return False, f"服务版本 {version!r} 非法"
        if ok:
            return True, None
        return False, f"版本 {version!r} 不满足约束 {constraint}"
    try:
        ok = bool(constraint(value))
    except Exception as exc:  # noqa: BLE001
        # 谓词异常按“不满足”处理（软等待语义），原因中保留异常消息便于诊断。
        return False, f"接口谓词异常: {exc}"
    return ok, None if ok else "接口谓词不满足"


def resolve_requirements(plugin: Any, inject: dict[str, Any]) -> dict[str, list[Any]]:
    """读取并规范化插件的 ``requirements`` 声明。

    声明来源：``@require`` 装饰器或类属性 ``requirements``（服务名 -> 约束或约束列表）。
    约束声明的服务名必须在 ``inject`` 中，否则立即抛出 :class:`InvalidRequirement`。
    """
    raw = getattr(plugin, "requirements", None) or {}
    if not isinstance(raw, Mapping):
        raise TypeError(f"invalid requirements declaration: {raw!r}")
    out: dict[str, list[Any]] = {}
    for name, constraints in raw.items():
        if name not in inject:
            raise InvalidRequirement(name, "service is not declared in inject")
        if isinstance(constraints, (str, Callable)):
            constraints = [constraints]
        if isinstance(constraints, (str, bytes)) or not isinstance(constraints, Iterable):
            raise InvalidRequirement(name, "constraints must be a string, callable, or a list of them")
        out[name] = [normalize_constraint(name, c) for c in constraints]
    return out


def resolve_plugin_config(plugin: Any, config: Any) -> Any:
    """按插件 ``Config`` 属性校验并转换配置，返回转换后的配置。

    支持两种形态：

    - callable（普通函数）：``Config(config)``，异常即校验失败；返回 ``None`` 表示
      校验通过且不做转换；
    - pydantic 模型类（检测 ``model_validate``）：走模型校验。

    校验失败抛出 :class:`ConfigValidationError`；形态不可识别抛出 :class:`TypeError`。
    """
    schema = getattr(plugin, "Config", None)
    if schema is None:
        return config
    name = getattr(plugin, "name", None) or getattr(plugin, "__name__", type(plugin).__name__)
    if inspect.isclass(schema) and hasattr(schema, "model_validate"):
        try:
            return schema.model_validate(config)
        except Exception as exc:
            raise ConfigValidationError(name, str(exc)) from exc
    if callable(schema):
        try:
            result = schema(config)
        except Exception as exc:
            raise ConfigValidationError(name, str(exc)) from exc
        return config if result is None else result
    raise TypeError(f"invalid plugin config schema for {name!r}")
