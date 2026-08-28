"""作用域路由：不可信插件隔离的协调式边界。

构建在 :class:`Context` 的监听器过滤（``filtered`` / ``context_filter``）之上，
参考 harness ``packages/core/scope`` 的 tag 路由模型：

- 每个作用域由独立 fiber 承担所有权（:func:`create_scope`），在其之下注册的
  监听器与服务随 dispose 整体回收；
- 派发时以 :func:`scope_target` 生成的**载体**为接收者：只有同 key 或祖先 key
  作用域的监听器（以及未标记的全局监听器）会被触发——事件只向上流，不向下；
- 这是**协调式**隔离：约束 Cordis 语义（事件、服务的可见性），不是恶意代码的
  真实安全边界。OS 级资源沙箱（子解释器 / subprocess / 文件系统限制）属于宿主
  环境职责。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .context import Context

__all__ = ["Scope", "bind_scope_parent", "create_scope", "scope_of", "scope_target"]

# 作用域父子链：child key → parent key（事件流向上游）。
_scope_parents: dict[Any, Any] = {}


def bind_scope_parent(child: Any, parent: Any) -> None:
    """声明作用域父子关系（child 向 parent 流动事件）。

    循环引用会被拒绝。重复绑定同一 child 会覆盖其旧父级。
    """
    cursor = parent
    seen: set[Any] = set()
    while cursor is not None:
        if cursor == child or cursor in seen:
            raise ValueError(f"scope parent chain cycle at {cursor!r}")
        seen.add(cursor)
        cursor = _scope_parents.get(cursor)
    _scope_parents[child] = parent


def scope_of(ctx: Context) -> Any | None:
    """读取上下文（含派生后代）最近的作用域 key。"""
    return getattr(ctx, "_scope_key", None)


def _scope_plugin(ctx: Context, config: Any) -> None:
    """作用域承载插件：无副作用，仅提供 fiber 所有权。"""
    return


@dataclass
class Scope:
    """一个已创建的作用域：注册入口与整体回收。"""

    ctx: Context
    _fiber: Any

    async def dispose(self) -> None:
        """回收作用域及其全部注册（幂等）。"""
        await self._fiber.dispose()


def create_scope(ctx: Context, key: Any, *, parent: Any | None = None) -> Scope:
    """在 *ctx* 下创建携带 *key* 标签的作用域。

    作用域上下文继承创建者上下文（含其 filter 与标签），其下注册的监听器随
    作用域 fiber 一起回收。*parent* 提供时声明祖先关系（事件向上流）。
    """
    if parent is not None:
        bind_scope_parent(key, parent)
    fiber = ctx.plugin(_scope_plugin)
    scoped = fiber.ctx
    scoped._scope_key = key
    return Scope(scoped, fiber)


class _Carrier:
    """带路由过滤器的透明载体（scope_target 的产物）。"""

    __slots__ = ("_scope_key", "context_filter")

    def __init__(self, predicate: Callable[..., bool], key: Any) -> None:
        self.context_filter = predicate
        self._scope_key = key


def scope_target(base: Any, key: Any) -> Any:
    """生成以 *key* 路由的派发接收者载体。

    保留 *base*（Context 或已有载体）上原有的过滤器，再叠加作用域路由：

    - 未标记作用域的监听器（未注册于任何 scope）全局放行；
    - 标记了作用域的监听器仅在同 key 或祖先 key 命中时放行——事件只向上流。
    """
    if isinstance(base, Context):
        base_filter = base._filter
    else:
        base_filter = getattr(base, "context_filter", None)

    def predicate(listener_ctx: Context) -> bool:
        if base_filter is not None and not base_filter(listener_ctx):
            return False
        tag = scope_of(listener_ctx)
        if tag is None:
            return True
        cursor = key
        while cursor is not None:
            if cursor == tag:
                return True
            cursor = _scope_parents.get(cursor)
        return False

    return _Carrier(predicate, key)
