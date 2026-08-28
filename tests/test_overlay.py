'''配置 overlay / 租户派生（internal/config waterfall）测试。

语义：

- 插件 config 激活前先经过 internal/config 瀑布链改写，再进入 Config 校验；
- 监听器只对注册者的严格后代 fiber 生效（root 上注册对全体生效）；
- 监听器签名 (fiber, config, next)；不调用 next 即短路。
'''

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cordis_py import ConfigValidationError, Context, FiberState, deep_merge

# ----------------------------------------------------------------------
# deep_merge
# ----------------------------------------------------------------------


def test_deep_merge_nested() -> None:
    '''递归深合并嵌套映射。'''
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    override = {"a": {"y": 3, "z": 4}}
    assert deep_merge(base, override) == {"a": {"x": 1, "y": 3, "z": 4}, "b": 1}


def test_deep_merge_replace_non_mapping() -> None:
    '''列表与标量整体替换。'''
    assert deep_merge({"items": [1, 2]}, {"items": [3]}) == {"items": [3]}
    assert deep_merge({"n": 1}, {"n": 5}) == {"n": 5}
    assert deep_merge({"deep": {"a": {"b": 2}}}, {"deep": "flat"}) == {"deep": "flat"}


def test_deep_merge_none_skipped() -> None:
    '''None 表示无配置，跳过。'''
    assert deep_merge({"a": 1}, None) == {"a": 1}
    assert deep_merge(None, {"a": 2}) == {"a": 2}
    assert deep_merge({"a": 1}, {"a": None}) == {"a": 1}


# ----------------------------------------------------------------------
# internal/config 瀑布链
# ----------------------------------------------------------------------


def test_root_listener_overrides_descendant_config() -> None:
    '''root 上注册的监听器改写所有后代插件配置。'''
    root = Context()
    seen: list[Any] = []

    async def overlay(fiber: Any, config: dict, next_fn: Any) -> Any:
        return deep_merge(await next_fn(), {"env": "prod", "log": {"level": "info"}})

    root.on("internal/config", overlay)

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config)

    root.plugin(plugin, {"name": "demo"})
    assert seen == [{"name": "demo", "env": "prod", "log": {"level": "info"}}]
    root.fiber.dispose_sync()


def test_listener_scoped_to_descendants() -> None:
    '''子 ctx 注册的监听器只对后代生效：兄弟与自身不触发。'''
    root = Context()
    seen: list[Any] = []
    child_seen: list[Any] = []
    grandchild_seen: list[Any] = []

    async def overlay(fiber: Any, config: dict, next_fn: Any) -> Any:
        return deep_merge(await next_fn(), {"tenant": "a"})

    def child(ctx: Context, config: dict) -> None:
        child_seen.append(config)
        ctx.on("internal/config", overlay)
        ctx.plugin(grandchild, {"name": "grandchild"})

    def grandchild(ctx: Context, config: dict) -> None:
        grandchild_seen.append(config)

    def sibling(ctx: Context, config: dict) -> None:
        seen.append(config)

    parent = root.plugin(child, {"name": "child"})
    # 等 child 插件加载完成后注册监听器（owner = child fiber）。
    # sibling 由 root 加载，不受 child 的监听器影响。
    root.plugin(sibling, {"name": "sibling"})

    assert child_seen == [{"name": "child"}]          # 监听器不作用于自身
    assert seen == [{"name": "sibling"}]              # 兄弟分支不触发
    assert grandchild_seen == [{"name": "grandchild", "tenant": "a"}]
    parent.dispose_sync()
    root.fiber.dispose_sync()


def test_listener_chain_order_and_short_circuit() -> None:
    '''多监听器按注册顺序链式；不调用 next 即短路。'''
    root = Context()
    seen: list[Any] = []

    async def first(fiber: Any, config: dict, next_fn: Any) -> Any:
        return deep_merge(await next_fn(), {"chain": {"first": True}})

    async def second(fiber: Any, config: dict, next_fn: Any) -> Any:
        return deep_merge(await next_fn(), {"chain": {"second": True}})

    async def short_circuit(fiber: Any, config: dict, next_fn: Any) -> Any:
        return {**config, "short": True}  # 不调用 next：后续监听器被否决

    root.on("internal/config", first)
    root.on("internal/config", second)
    root.on("internal/config", short_circuit)

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config)

    root.plugin(plugin, {})
    assert seen == [{"chain": {"first": True, "second": True}, "short": True}]
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# update 与 Config 校验的联动
# ----------------------------------------------------------------------


def test_update_reapplies_overlay() -> None:
    '''update() 重载同样经过 overlay。'''
    root = Context()
    seen: list[Any] = []

    async def overlay(fiber: Any, config: dict, next_fn: Any) -> Any:
        return deep_merge(await next_fn(), {"env": "prod"})

    root.on("internal/config", overlay)

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config)

    f = root.plugin(plugin, {"v": 1})
    assert seen == [{"v": 1, "env": "prod"}]
    f.update_sync({"v": 2})
    assert seen == [{"v": 1, "env": "prod"}, {"v": 2, "env": "prod"}]
    root.fiber.dispose_sync()


def test_overlay_result_goes_through_config_validation() -> None:
    '''overlay 改写后的 config 再进入 Config 校验：非法结果导致加载失败。'''
    root = Context()

    async def overlay(fiber: Any, config: dict, next_fn: Any) -> Any:
        return {"mode": "off"}  # 校验器要求 on

    root.on("internal/config", overlay)

    def validate(config: dict) -> dict:
        assert config.get("mode") == "on", "mode must be on"
        return config

    def plugin(ctx: Context, config: dict) -> None:
        return None

    plugin.Config = validate  # type: ignore[attr-defined]
    with pytest.raises(ConfigValidationError, match="mode must be on"):
        root.plugin(plugin, {"mode": "on"})
    assert root._fibers[-1].state == FiberState.FAILED
    root.fiber.dispose_sync()


async def test_async_handler_awaits_next() -> None:
    '''async 监听器通过 await next() 获取下游结果再改写。'''
    root = Context()
    seen: list[Any] = []

    async def overlay(fiber: Any, config: dict, next_fn: Any) -> Any:
        base = await next_fn()
        await asyncio.sleep(0)
        return deep_merge(base, {"derive": "async"})

    root.on("internal/config", overlay)

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config)

    f = root.plugin(plugin, {"a": 1})
    await f
    assert seen == [{"a": 1, "derive": "async"}]
    await root.fiber.dispose()


async def test_async_load_path_with_overlay() -> None:
    '''异步加载路径：overlay 在 await 语义下生效。'''
    root = Context()
    seen: list[Any] = []

    async def overlay(fiber: Any, config: dict, next_fn: Any) -> Any:
        return deep_merge(await next_fn(), {"scope": "tenant-a"})

    root.on("internal/config", overlay)

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config)

    f = root.plugin(plugin, {"name": "x"})
    await f
    assert seen == [{"name": "x", "scope": "tenant-a"}]
    await root.fiber.dispose()


def test_sync_handler_without_await_next() -> None:
    '''同步监听器直接返回改写值（不调用 next）即短路。'''
    root = Context()
    seen: list[Any] = []

    def overlay(fiber: Any, config: dict, next_fn: Any) -> Any:
        return {**config, "from_sync": True}

    root.on("internal/config", overlay)

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config)

    root.plugin(plugin, {"a": 1})
    assert seen == [{"a": 1, "from_sync": True}]
    root.fiber.dispose_sync()
