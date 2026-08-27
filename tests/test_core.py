from __future__ import annotations

import asyncio

import pytest

from cordis_py import Context, Service, inject, UndeclaredAccess


class Greeter(Service):
    def __init__(self, ctx: Context):
        super().__init__(ctx, "greeter")

    def hello(self, name: str) -> str:
        return f"Hello, {name}!"


async def test_service_plugin_and_inject() -> None:
    root = Context()
    await root.plugin(Greeter)

    calls: list[str] = []

    @inject("greeter")
    def plugin(ctx: Context, config: dict) -> None:
        calls.append(ctx.greeter.hello("world"))
        ctx.on("app/ready", lambda msg: calls.append(f"event:{msg}"))
        return None

    fiber = root.plugin(plugin)
    await fiber
    root.emit("app/ready", "ok")
    assert calls == ["Hello, world!", "event:ok"]
    await fiber.dispose()
    await root.fiber.dispose()


async def test_dependency_wait_and_unload() -> None:
    root = Context()
    log: list[str] = []

    def consumer(ctx: Context, config: dict) -> None:
        log.append(f"active:{ctx.model}")
        return lambda: log.append("consumer-cleanup")

    consumer.inject = ["model"]
    consumer_fiber = root.plugin(consumer)
    await asyncio.sleep(0)
    assert consumer_fiber.state.name == "PENDING"

    def provider(ctx: Context, config: dict) -> None:
        log.append("provider-active")
        ctx.provide("model", {"name": "test"})
        return lambda: log.append("provider-cleanup")

    provider_fiber = root.plugin(provider)
    await provider_fiber
    await asyncio.sleep(0)
    assert consumer_fiber.state.name == "ACTIVE"
    assert log == ["provider-active", "active:{'name': 'test'}"]

    await provider_fiber.dispose()
    await asyncio.sleep(0)
    assert consumer_fiber.state.name == "PENDING"
    assert "consumer-cleanup" in log
    assert "provider-cleanup" in log

    await root.fiber.dispose()


async def test_effect_cleanup_lifo() -> None:
    root = Context()
    log: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        ctx.effect(lambda: (log.append("first"), lambda: log.append("undo-first"))[1])
        ctx.effect(lambda: (log.append("second"), lambda: log.append("undo-second"))[1])
        return None

    fiber = root.plugin(plugin)
    await fiber
    assert log == ["first", "second"]
    await fiber.dispose()
    assert log == ["first", "second", "undo-second", "undo-first"]
    await root.fiber.dispose()


async def test_undeclared_access_is_rejected() -> None:
    root = Context()

    def greeter(ctx: Context, config: dict) -> None:
        ctx.provide("greeter", object())

    root.plugin(greeter)

    def bad(ctx: Context, config: dict) -> None:
        ctx.greeter  # noqa: B018

    fiber = root.plugin(bad)
    with pytest.raises(UndeclaredAccess):
        await fiber
    await root.fiber.dispose()


async def test_serial_short_circuit() -> None:
    root = Context()
    seen: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        ctx.on("e", lambda *a: (seen.append("a"), "stop")[1])
        ctx.on("e", lambda *a: seen.append("b"))
        return None

    fiber = root.plugin(plugin)
    await fiber
    result = await root.serial("e")
    assert result == "stop"
    assert seen == ["a"]
    await root.fiber.dispose()


async def test_isolate_keeps_services_separate() -> None:
    root = Context()
    tenant_a = root.isolate("db", "a")
    tenant_b = root.isolate("db", "b")

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("db", {"tenant": ctx._isolation["db"]})

    await tenant_a.plugin(provider)
    await tenant_b.plugin(provider)

    seen: list[str] = []

    def consumer(ctx: Context, config: dict) -> None:
        seen.append(ctx.db["tenant"])

    consumer.inject = ["db"]
    await tenant_a.plugin(consumer)
    assert seen == ["a"]

    await root.fiber.dispose()
