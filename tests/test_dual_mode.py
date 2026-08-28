'''同步/异步双模式封装测试。

同步模式的核心约定：

- 没有运行事件循环时，生命周期转换与同步效果由 ``drive_sync`` 内联驱动，
  调用方返回时加载/卸载已经完成；
- 有运行事件循环时，走 ``asyncio`` 后台调度（与 Node 版 Cordis 的异步行为一致）；
- 同步模式下遇到必须依赖事件循环的操作（异步插件、异步效果、异步监听器）
  会抛出 :class:`AsyncRequiredError`，指明改用异步 API。
'''

from __future__ import annotations

import asyncio

import pytest

from cordis_py import AsyncRequiredError, Context, FiberState

# ----------------------------------------------------------------------
# 同步加载 / 响应式依赖
# ----------------------------------------------------------------------


def test_sync_load_and_reactive_activation() -> None:
    '''同步模式下：先注册的消费者等待提供者，提供后内联激活。'''
    root = Context()
    log: list[str] = []

    def consumer(ctx: Context, config: dict) -> None:
        log.append(f"consumer:{ctx.model['n']}")

    consumer.inject = ["model"]

    def provider(ctx: Context, config: dict) -> None:
        log.append("provider-start")
        ctx.provide("model", {"n": 1})
        return lambda: log.append("provider-stop")

    c = root.plugin(consumer)  # 先注册消费者，此时依赖缺失
    assert c.state == FiberState.PENDING
    p = root.plugin(provider)  # 提供后消费者应内联激活
    assert p.state == FiberState.ACTIVE
    assert c.state == FiberState.ACTIVE
    assert log == ["provider-start", "consumer:1"]

    root.fiber.dispose_sync()


def test_sync_consumer_pending_when_provider_missing() -> None:
    '''同步模式下：依赖始终缺失的消费者保持 PENDING。'''
    root = Context()

    def consumer(ctx: Context, config: dict) -> None:
        return None

    consumer.inject = ["model"]
    c = root.plugin(consumer)
    assert c.state == FiberState.PENDING
    assert c.error is None
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# 同步卸载
# ----------------------------------------------------------------------


def test_sync_dispose_is_reverse_load() -> None:
    '''同步卸载按加载顺序的逆序执行，效果按 LIFO 清理。'''
    root = Context()
    log: list[str] = []

    def make(name: str):
        def plugin(ctx: Context, config: dict) -> None:
            log.append(f"{name}-start")
            return lambda: log.append(f"{name}-stop")

        return plugin

    root.plugin(make("a"))
    root.plugin(make("b"))
    assert log == ["a-start", "b-start"]

    root.fiber.dispose_sync()
    assert log == ["a-start", "b-start", "b-stop", "a-stop"]


def test_sync_parent_dispose_cascades() -> None:
    '''同步模式下父 fiber 卸载会级联清理子 fiber。'''
    root = Context()
    log: list[str] = []

    def child(ctx: Context, config: dict) -> None:
        log.append("child-start")
        return lambda: log.append("child-stop")

    def parent(ctx: Context, config: dict) -> None:
        log.append("parent-start")
        ctx.plugin(child)
        return lambda: log.append("parent-stop")

    f = root.plugin(parent)
    assert f.state == FiberState.ACTIVE
    f.dispose_sync()
    assert log == ["parent-start", "child-start", "child-stop", "parent-stop"]
    root.fiber.dispose_sync()


def test_sync_dispose_twice_is_idempotent() -> None:
    '''重复同步卸载不报错、不重复清理。'''
    root = Context()
    log: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        return lambda: log.append("stop")

    f = root.plugin(plugin)
    f.dispose_sync()
    f.dispose_sync()
    assert log == ["stop"]
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# 同步重启 / 配置更新
# ----------------------------------------------------------------------


def test_sync_restart_and_update() -> None:
    '''同步重启与配置更新：先清理旧效果再重新加载新配置。'''
    root = Context()
    log: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        log.append(f"start:{config}")
        return lambda: log.append(f"stop:{config}")

    f = root.plugin(plugin, {"v": 1})
    assert log == ["start:{'v': 1}"]

    f.update_sync({"v": 2})
    assert log == ["start:{'v': 1}", "stop:{'v': 1}", "start:{'v': 2}"]

    f.restart_sync()
    assert log == [
        "start:{'v': 1}",
        "stop:{'v': 1}",
        "start:{'v': 2}",
        "stop:{'v': 2}",
        "start:{'v': 2}",
    ]
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# 同步错误传播与边界
# ----------------------------------------------------------------------


def test_sync_load_failure_propagates() -> None:
    '''同步模式下插件加载失败由 plugin() 立即抛出。'''
    root = Context()

    def bad(ctx: Context, config: dict) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        root.plugin(bad)
    # 错误已记录在 fiber 上，且状态为 FAILED。
    assert root._fibers[-1].state == FiberState.FAILED
    assert type(root._fibers[-1].error) is ValueError
    root.fiber.dispose_sync()


def test_sync_async_plugin_requires_loop() -> None:
    '''同步模式下加载需要事件循环的异步插件抛出 AsyncRequiredError。'''
    root = Context()

    async def plugin(ctx: Context, config: dict) -> None:
        await asyncio.sleep(0)

    with pytest.raises(AsyncRequiredError):
        root.plugin(plugin)
    root.fiber.dispose_sync()


def test_sync_effect_requires_loop() -> None:
    '''同步模式下注册需要事件循环的异步效果抛出 AsyncRequiredError。'''
    root = Context()

    async def coro() -> None:
        await asyncio.sleep(0)

    def plugin(ctx: Context, config: dict) -> None:
        ctx.effect(lambda: coro())

    with pytest.raises(AsyncRequiredError):
        root.plugin(plugin)
    root.fiber.dispose_sync()


def test_sync_emit_async_listener_raises() -> None:
    '''同步模式 emit 遇到异步监听器：先执行同步监听器，再抛出边界错误。'''
    root = Context()
    seen: list[str] = []

    async def async_listener() -> None:
        seen.append("never")  # 不应被执行

    def plugin(ctx: Context, config: dict) -> None:
        ctx.on("e", lambda: seen.append("sync"))
        ctx.on("e", async_listener)

    f = root.plugin(plugin)
    with pytest.raises(AsyncRequiredError):
        root.emit("e")
    assert seen == ["sync"]  # 同步监听器已执行，异步监听器被显式拒绝
    f.dispose_sync()


def test_sync_bail_async_listener_raises() -> None:
    '''同步模式 bail 遇到异步监听器抛出 AsyncRequiredError。'''
    root = Context()

    async def async_listener() -> None:
        return None

    def plugin(ctx: Context, config: dict) -> None:
        ctx.on("e", async_listener)

    f = root.plugin(plugin)
    with pytest.raises(AsyncRequiredError):
        root.bail("e")
    f.dispose_sync()


async def test_sync_api_under_loop_raises() -> None:
    '''有运行事件循环时调用同步 API 抛出 AsyncRequiredError。'''
    root = Context()
    f = root.plugin(lambda ctx, config: None)
    await f
    with pytest.raises(AsyncRequiredError):
        f.dispose_sync()
    with pytest.raises(AsyncRequiredError):
        f.restart_sync()
    with pytest.raises(AsyncRequiredError):
        f.update_sync({})
    await root.fiber.dispose()


# ----------------------------------------------------------------------
# 异步路径回归
# ----------------------------------------------------------------------


async def test_async_restart_reruns_plugin() -> None:
    '''异步 restart：先清理旧效果再重新加载（卸载先于加载）。'''
    root = Context()
    log: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        ctx.effect(lambda: (log.append("setup"), lambda: log.append("teardown"))[1])

    f = root.plugin(plugin)
    await f
    assert log == ["setup"]
    await f.restart()
    assert log == ["setup", "teardown", "setup"]
    await root.fiber.dispose()


async def test_async_update_picks_new_config() -> None:
    '''异步 update：应用新配置并重启，旧效果被清理。'''
    root = Context()
    log: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        log.append(f"v:{config}")
        return lambda: log.append("stop")

    f = root.plugin(plugin, 1)
    await f
    await f.update(2)
    assert log == ["v:1", "stop", "v:2"]
    await root.fiber.dispose()
