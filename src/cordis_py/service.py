"""Service 基类与依赖注入装饰器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar, overload

from .utils import merge_config

T = TypeVar("T")


class Service:
    """注册到 Cordis Context 上的服务基类。

    子类调用 ``super().__init__(ctx, name)``，或使用类级 ``provide`` 属性指定服务名。
    类级 ``version`` 属性可声明服务版本，构造参数 *version* 优先级更高。
    """

    provide: str | None = None
    version: str | None = None

    def __init__(self, ctx: Any, name: str | None = None, *, version: str | None = None) -> None:
        self.ctx = ctx
        self.name = name or self.provide or type(self).__name__.lower()
        self.version = version if version is not None else type(self).version
        ctx.provide(self.name, self, version=self.version)

    def resolve_config(self, base: Any = None, head: Any = None) -> Any:
        """合并本服务名上的拦截配置。

        优先级从低到高：``base`` < 祖先上下文的 ``intercept()`` 条目 < 当前上下文条目
        < ``head``。与 Node 版 Cordis 的 ``Service[symbols.resolveConfig]`` 语义对齐：
        条目为映射时浅合并，非映射条目整体替换；没有任何配置时返回 ``None``。
        """
        merged = self.ctx.intercept_config(self.name)
        if base is not None:
            merged = merge_config(base, merged)
        if head is not None:
            merged = merge_config(merged, head)
        return merged


def _attach_inject(target: Any, deps: list[str] | Mapping[str, Any]) -> Any:
    old = getattr(target, "inject", None)
    if old is None:
        combined: dict[str, Any] = {}
    elif isinstance(old, list):
        combined = {name: None for name in old}
    elif isinstance(old, Mapping):
        combined = dict(old)
    else:
        combined = {}

    if isinstance(deps, list):
        for name in deps:
            combined.setdefault(name, None)
    else:
        for name, config in deps.items():
            combined.setdefault(name, config)
    target.inject = combined  # type: ignore[attr-defined]
    return target


def require(name: str, constraint: Any):
    """声明插件对服务的版本或接口约束。

    *constraint* 为 PEP 440 specifier 字符串（如 ``">=1.0"``）或接收服务对象的
    callable 谓词（如 ``lambda svc: hasattr(svc, "query")``，返回真值即满足）。
    装饰器可多次叠加：同名服务的多个约束为 AND 关系；也可以与类属性
    ``requirements`` 并存（装饰器会合并已有声明）。
    约束不满足时插件保持 PENDING（软等待），不构成错误。
    """

    def decorator(target: T) -> T:
        raw = getattr(target, "requirements", None)
        reqs = dict(raw) if isinstance(raw, Mapping) else {}
        reqs.setdefault(name, []).append(constraint)
        target.requirements = reqs  # type: ignore[attr-defined]
        return target

    return decorator


@overload
def inject(name: str, /) -> Callable[[T], T]: ...
@overload
def inject(names: list[str], /) -> Callable[[T], T]: ...
@overload
def inject(deps: Mapping[str, Any], /) -> Callable[[T], T]: ...


def inject(deps: str | list[str] | Mapping[str, Any], /):
    """在插件函数或类上声明所需服务。

    用法示例：

        @inject("model")
        def my_plugin(ctx, config): ...

        @inject(["model", "storage"])
        class MyPlugin: ...

        @inject({"model": {"timeout": 10}})
        def another(ctx, config): ...
    """
    if isinstance(deps, str):
        dep_list = [deps]
    elif isinstance(deps, list):
        dep_list = deps
    elif isinstance(deps, Mapping):
        dep_map = dict(deps)

        def decorator(target: T) -> T:
            return _attach_inject(target, dep_map)  # type: ignore[return-value]

        return decorator
    else:
        raise TypeError("inject() expects a service name, a list of names, or a mapping")

    def decorator(target: T) -> T:
        return _attach_inject(target, dep_list)  # type: ignore[return-value]

    return decorator


__all__ = ["Service", "inject", "require"]
