'''服务版本约束、接口校验与配置校验测试。

核心语义：

- 约束不满足时消费者保持 PENDING（软等待，对齐 Node 版 Service.check），
  提供方变化后自动重新评估；
- 提供方未声明版本而消费方有版本约束时保守等待；
- require 声明本身无效（未在 inject 中、specifier 语法错误）为声明期错误；
- Config 校验失败为加载错误（fiber FAILED），不会是静默等待。
'''

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from cordis_py import (
    ConfigValidationError,
    Context,
    FiberState,
    InvalidRequirement,
    Loader,
    Service,
    inject,
    require,
)

# ----------------------------------------------------------------------
# 提供方版本声明
# ----------------------------------------------------------------------


def test_service_version_via_class_attribute() -> None:
    '''类属性 version 声明服务版本。'''
    root = Context()

    class Model(Service):
        version = "1.0.0"

        def __init__(self, ctx: Context) -> None:
            super().__init__(ctx, "model")

    root.plugin(Model)
    assert root.services["model"].version == "1.0.0"
    root.fiber.dispose_sync()


def test_service_version_via_constructor() -> None:
    '''构造参数 version 优先于类属性。'''
    root = Context()

    class Model(Service):
        version = "1.0.0"

        def __init__(self, ctx: Context) -> None:
            super().__init__(ctx, "model", version="2.0.0")

    root.plugin(Model)
    assert root.services["model"].version == "2.0.0"
    root.fiber.dispose_sync()


def test_provide_version_keyword() -> None:
    '''自由插件通过 provide(version=...) 声明版本。'''
    root = Context()

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("model", object(), version="0.9.0")

    root.plugin(provider)
    entry = root._services[(None, "model")]
    assert entry.version == "0.9.0"
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# 版本约束（软等待）
# ----------------------------------------------------------------------


def test_version_constraint_matches_and_activates() -> None:
    '''版本满足约束时消费者正常激活。'''
    root = Context()
    seen: list[str] = []

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("model", object(), version="1.2.0")

    root.plugin(provider)

    @inject("model")
    @require("model", ">=1.0")
    def consumer(ctx: Context, config: dict) -> None:
        seen.append("active")

    root.plugin(consumer)
    assert seen == ["active"]
    root.fiber.dispose_sync()


def test_version_constraint_waits_then_activates() -> None:
    '''版本不匹配时 PENDING；提供方换成匹配版本后自动激活。'''
    root = Context()
    seen: list[str] = []

    @inject("model")
    @require("model", ">=2.0")
    def consumer(ctx: Context, config: dict) -> None:
        seen.append("active")

    c = root.plugin(consumer)
    assert c.state == FiberState.PENDING
    assert c.unsatisfied["model"] == "服务未提供"

    def provider_v1(ctx: Context, config: dict) -> None:
        ctx.provide("model", object(), version="1.0.0")

    p1 = root.plugin(provider_v1)
    assert c.state == FiberState.PENDING
    assert "不满足约束" in c.unsatisfied["model"]
    assert c.unsatisfied["model"] == '版本 \'1.0.0\' 不满足约束 >=2.0'

    # 卸载旧提供方，提供满足约束的版本。
    p1.dispose_sync()

    def provider_v2(ctx: Context, config: dict) -> None:
        ctx.provide("model", object(), version="2.5.0")

    root.plugin(provider_v2)
    assert c.state == FiberState.ACTIVE
    assert c.unsatisfied == {}
    assert seen == ["active"]
    root.fiber.dispose_sync()


def test_version_constraint_missing_version_waits() -> None:
    '''提供方未声明版本 + 消费方有版本约束：保守等待。'''
    root = Context()

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("model", object())

    root.plugin(provider)

    @inject("model")
    @require("model", ">=1.0")
    def consumer(ctx: Context, config: dict) -> None:
        return None

    c = root.plugin(consumer)
    assert c.state == FiberState.PENDING
    assert c.unsatisfied["model"] == "服务未声明版本"
    root.fiber.dispose_sync()


def test_multiple_constraints_and() -> None:
    '''同名服务的多个约束为 AND：全部满足才激活。'''
    root = Context()

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("model", {"name": "m1"}, version="1.0.0")

    root.plugin(provider)
    seen: list[str] = []

    @inject("model")
    @require("model", ">=1.0,<2.0")
    @require("model", lambda svc: svc["name"] == "m1")
    def consumer(ctx: Context, config: dict) -> None:
        seen.append("active")

    root.plugin(consumer)
    assert seen == ["active"]
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# 接口谓词
# ----------------------------------------------------------------------


def test_interface_predicate_waits_and_activates() -> None:
    '''接口谓词不满足时等待，满足后自动激活。'''
    root = Context()
    seen: list[str] = []

    @inject("db")
    @require("db", lambda svc: hasattr(svc, "query"))
    def consumer(ctx: Context, config: dict) -> None:
        seen.append("active")

    c = root.plugin(consumer)
    assert c.state == FiberState.PENDING

    def bad_provider(ctx: Context, config: dict) -> None:
        ctx.provide("db", object())  # 没有 query 方法

    root.plugin(bad_provider)
    assert c.state == FiberState.PENDING
    assert c.unsatisfied["db"] == "接口谓词不满足"
    root.fiber.dispose_sync()


def test_interface_predicate_exception_waits() -> None:
    '''谓词抛异常视为不满足并记录异常消息。'''
    root = Context()

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("db", object())

    root.plugin(provider)

    @inject("db")
    @require("db", lambda svc: fails(svc))
    def consumer(ctx: Context, config: dict) -> None:
        return None

    def fails(svc: Any) -> bool:
        raise RuntimeError("probe failed")

    c = root.plugin(consumer)
    assert c.state == FiberState.PENDING
    assert c.unsatisfied["db"] == "接口谓词异常: probe failed"
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# 声明期错误
# ----------------------------------------------------------------------


def test_require_undeclared_service_raises() -> None:
    '''require 的服务未在 inject 中声明：注册时立即报错。'''
    root = Context()

    @inject("model")
    @require("model", ">=1.0")
    @require("db", ">=1.0")  # db 未在 inject 中声明
    def plugin(ctx: Context, config: dict) -> None:
        return None

    with pytest.raises(InvalidRequirement, match="db"):
        root.plugin(plugin)
    root.fiber.dispose_sync()


def test_invalid_specifier_raises() -> None:
    '''非法 specifier 语法：注册时立即报错。'''
    root = Context()

    @inject("model")
    @require("model", "not-a-specifier!!")
    def plugin(ctx: Context, config: dict) -> None:
        return None

    with pytest.raises(InvalidRequirement, match="invalid specifier"):
        root.plugin(plugin)
    root.fiber.dispose_sync()


# ----------------------------------------------------------------------
# 配置校验
# ----------------------------------------------------------------------

VALIDATOR_CALLS: list[Any] = []


def test_config_callable_transform() -> None:
    '''Config callable 可校验并转换配置。'''
    root = Context()
    seen: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config["timeout"])

    def validate(config: dict) -> dict:
        assert config.get("name"), "name is required"
        return {"name": config["name"], "timeout": 5}

    plugin.Config = validate  # type: ignore[attr-defined]
    root.plugin(plugin, {"name": "demo"})
    assert seen == [5]
    root.fiber.dispose_sync()


def test_config_validation_failure_fails() -> None:
    '''Config 校验失败：fiber FAILED；同步 plugin() 立即抛出。'''
    root = Context()
    seen: list[str] = []

    def plugin(ctx: Context, config: dict) -> None:
        seen.append("should-not-run")

    def validate(config: dict) -> dict:
        raise ValueError("bad config")

    plugin.Config = validate  # type: ignore[attr-defined]
    with pytest.raises(ConfigValidationError, match="bad config"):
        root.plugin(plugin, {})
    assert seen == []
    assert root._fibers[-1].state == FiberState.FAILED
    assert isinstance(root._fibers[-1].error, ConfigValidationError)
    root.fiber.dispose_sync()


def test_config_callable_none_keeps_config() -> None:
    '''Config callable 返回 None：校验通过且不做转换。'''
    root = Context()
    seen: list[Any] = []

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config)

    plugin.Config = lambda config: None  # type: ignore[attr-defined]
    root.plugin(plugin, {"a": 1})
    assert seen == [{"a": 1}]
    root.fiber.dispose_sync()


def test_config_pydantic_model() -> None:
    '''Config 为 pydantic 模型类时走模型校验。'''
    pydantic = pytest.importorskip("pydantic")
    root = Context()
    seen: list[Any] = []

    class PluginConf(pydantic.BaseModel):
        timeout: int

    def plugin(ctx: Context, config: dict) -> None:
        seen.append(config.timeout)

    plugin.Config = PluginConf  # type: ignore[attr-defined]
    root.plugin(plugin, {"timeout": 10})
    assert seen == [10]

    def bad(ctx: Context, config: dict) -> None:
        return None

    bad.Config = PluginConf  # type: ignore[attr-defined]
    with pytest.raises(ConfigValidationError):
        root.plugin(bad, {"timeout": "nope"})
    root.fiber.dispose_sync()


async def test_loader_config_update_revalidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''Loader 配置变更触发 update → 重新校验，失败时 update 重抛并标记 FAILED。'''
    module = tmp_path / "cfg_plugin.py"
    module.write_text(
        """
from cordis_py import Context

def validate(config: dict) -> dict:
    assert config.get("mode") == "on", "mode must be on"
    return config

def plugin(ctx: Context, config: dict):
    return None

plugin.Config = validate
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    root = Context()
    loader = Loader(root)
    await loader.reconcile([{"id": "cfg", "url": "cfg_plugin:plugin", "config": {"mode": "on"}}])
    assert "cfg" in loader.fibers

    # 配置变为非法：update 的 wait() 重新抛出校验错误，fiber 标记为 FAILED。
    with pytest.raises(ConfigValidationError, match="mode must be on"):
        await loader.reconcile([{"id": "cfg", "url": "cfg_plugin:plugin", "config": {"mode": "off"}}])
    assert loader.fibers["cfg"].state == FiberState.FAILED
    assert isinstance(loader.fibers["cfg"].error, ConfigValidationError)
    await loader.dispose()
    await root.fiber.dispose()


# ----------------------------------------------------------------------
# 同步/异步路径与回归
# ----------------------------------------------------------------------


async def test_async_version_wait_then_activate() -> None:
    '''异步路径：版本约束等待与自动激活。'''
    root = Context()
    seen: list[str] = []

    @inject("model")
    @require("model", "==1.0.0")
    def consumer(ctx: Context, config: dict) -> None:
        seen.append("active")

    c = root.plugin(consumer)
    await asyncio.sleep(0)
    assert c.state == FiberState.PENDING
    assert c.unsatisfied["model"] == "服务未提供"

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("model", object(), version="1.0.0")

    p = root.plugin(provider)
    await p  # 等提供方加载完成（其中会调度消费者）
    await c  # 等消费者激活
    assert c.state == FiberState.ACTIVE
    assert seen == ["active"]
    await root.fiber.dispose()


def test_sync_version_immediate_activation() -> None:
    '''同步路径：提供方在消费者之前加载，版本满足即激活。'''
    root = Context()
    seen: list[str] = []

    class Model(Service):
        version = "3.0.0"

        def __init__(self, ctx: Context) -> None:
            super().__init__(ctx, "model")

    root.plugin(Model)

    @inject("model")
    @require("model", ">=2.0")
    def consumer(ctx: Context, config: dict) -> None:
        seen.append("active")

    c = root.plugin(consumer)
    assert c.state == FiberState.ACTIVE
    assert seen == ["active"]
    root.fiber.dispose_sync()
