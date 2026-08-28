from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cordis_py import Context, bind_scope_parent, create_scope, scope_of, scope_target


async def test_global_listener_bypasses_filter() -> None:
    root = Context()
    heard: list[str] = []
    veto = root.filtered(lambda owner: False)
    root.on("ping", lambda value: heard.append(f"global:{value}"), global_=True)
    veto.on("ping", lambda value: heard.append(f"local:{value}"))
    root.emit("ping", "a", receiver=veto)
    assert heard == ["global:a"]


async def test_filtered_predicate_receives_registration_ctx() -> None:
    root = Context()
    heard: list[str] = []
    a = root.filtered(lambda owner: owner is a)
    b = root.filtered(lambda owner: owner is b)
    a.on("ping", lambda value: heard.append(f"a:{value}"))
    b.on("ping", lambda value: heard.append(f"b:{value}"))
    root.emit("ping", "x", receiver=a)
    assert heard == ["a:x"]
    heard.clear()
    root.emit("ping", "y", receiver=b)
    assert heard == ["b:y"]


async def test_receiver_carrier_with_context_filter() -> None:
    root = Context()
    heard: list[str] = []
    a = root.filtered(lambda owner: owner is a)
    a.on("ping", lambda value: heard.append(f"a:{value}"))
    root.on("ping", lambda value: heard.append(f"root:{value}"))
    carrier = SimpleNamespace(context_filter=lambda owner: owner is a)
    root.emit("ping", "x", receiver=carrier)
    assert heard == ["a:x"]
    # 未设置过滤器的 receiver 行为不变。
    heard.clear()
    root.emit("ping", "y", receiver=SimpleNamespace())
    assert heard == ["a:y", "root:y"]


async def test_filter_applies_to_all_dispatch_modes() -> None:
    root = Context()
    seen: list[str] = []

    def handler(*args: Any) -> None:
        seen.append(args[0])

    a = root.filtered(lambda owner: owner is a)
    a.on("evt", handler)
    root.on("evt", lambda *args: seen.append("root"))

    root.emit("evt", "emit", receiver=a)
    assert seen == ["emit"]
    await root.parallel("evt", "parallel", receiver=a)
    await root.serial("evt", "serial", receiver=a)
    root.bail("evt", "bail", receiver=a)
    await root.waterfall("evt", "waterfall", receiver=a)
    assert seen == ["emit", "parallel", "serial", "bail", "waterfall"]


async def test_filter_inherited_by_descendants() -> None:
    root = Context()
    a = root.filtered(lambda owner: True)
    b = a.isolate("svc", "realm-x")
    assert b._filter is a._filter
    # 后代上下文仍未携带作用域标签（scope 通过创建时显式标记）。
    assert scope_of(b) is None


async def test_waterfall_fallback_tail() -> None:
    """链尾 fallback：对齐 Node waterfall 的最内层 next（中间件语义）。"""
    root = Context()
    seen: list[str] = []

    async def middleware(tenant: str, request: dict, next_: Any) -> Any:
        seen.append(tenant)
        return await next_()

    def quota(tenant: str, request: dict, next_: Any) -> Any:
        return {"status": 429, "tenant": tenant}

    root.on("http/request", middleware)
    result = await root.waterfall(
        "http/request", "acme", {"path": "/x"}, fallback=lambda *args: f"handled:{args}"
    )
    assert seen == ["acme"]
    assert result == "handled:('acme', {'path': '/x'})"

    root.on("http/request", quota)
    result = await root.waterfall(
        "http/request", "acme", {"path": "/x"}, fallback=lambda *args: "handled"
    )
    assert result == {"status": 429, "tenant": "acme"}


async def test_scope_routes_upward_only() -> None:
    root = Context()
    heard: list[str] = []
    root.on("ping", lambda value: heard.append("global"))
    scope_a = create_scope(root, "A")
    scope_b = create_scope(root, "B")
    scope_a.ctx.on("ping", lambda value: heard.append("A"))
    scope_b.ctx.on("ping", lambda value: heard.append("B"))

    scope_a.ctx.on("ping", lambda value: heard.append("A!"), global_=True)

    root.emit("ping", "a", receiver=scope_target(root, "A"))
    assert heard == ["global", "A", "A!"]
    heard.clear()
    root.emit("ping", "b", receiver=scope_target(root, "B"))
    assert heard == ["global", "B", "A!"]

    # 祖先关系：事件向上流（B 的派发可见 A），不向下流。
    bind_scope_parent("B", "A")
    heard.clear()
    root.emit("ping", "x", receiver=scope_target(root, "B"))
    assert heard == ["global", "A", "B", "A!"]
    heard.clear()
    root.emit("ping", "y", receiver=scope_target(root, "A"))
    assert heard == ["global", "A", "A!"]


async def test_scope_of_derived_contexts() -> None:
    root = Context()
    scope = create_scope(root, "sess-1")
    assert scope_of(scope.ctx) == "sess-1"
    # 派生上下文集承作用域标签。
    assert scope_of(scope.ctx.isolate("svc", "r")) == "sess-1"
    assert scope_of(root) is None


async def test_scope_dispose_removes_registrations() -> None:
    root = Context()
    heard: list[str] = []
    root.on("ping", lambda value: heard.append("global"))
    scope = create_scope(root, "A")
    scope.ctx.on("ping", lambda value: heard.append("A"))
    await scope.dispose()
    await scope.dispose()  # 幂等
    root.emit("ping", "x", receiver=scope_target(root, "A"))
    assert heard == ["global"]


def test_scope_parent_cycle_rejected() -> None:
    bind_scope_parent("C1", "C2")
    with pytest.raises(ValueError, match="cycle"):
        bind_scope_parent("C2", "C1")


async def test_untrusted_plugin_workflow() -> None:
    root = Context()
    heard: list[str] = []
    untrusted = create_scope(root, "untrusted/plugin-x")
    trusted = create_scope(root, "trusted/area")
    # 不可信插件试图监听外部信道的派发。
    untrusted.ctx.on("cloud/update", lambda value: heard.append(f"untrusted:{value}"))
    trusted.ctx.on("cloud/update", lambda value: heard.append(f"trusted:{value}"))

    # 可信侧以可信载体派发：不可信监听器被过滤。
    root.emit("cloud/update", "1", receiver=scope_target(root, "trusted/area"))
    assert heard == ["trusted:1"]
    # 不可信插件内部派发：只见自身（及其祖先）。
    heard.clear()
    root.emit("cloud/update", "2", receiver=scope_target(root, "untrusted/plugin-x"))
    assert heard == ["untrusted:2"]

    await untrusted.dispose()
    await trusted.dispose()
