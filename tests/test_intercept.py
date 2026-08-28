'''intercept 服务级配置拦截测试。

语义（与 Node 版 Cordis 的 ``Service[symbols.resolveConfig]`` 对齐）：

- ``ctx.intercept(name, config)`` 在子上下文的拦截链上登记配置；
- 插件在 inject 声明中的非空配置也会进入其上下文的拦截链；
- 合并时祖先条目先应用，越靠近当前上下文的条目优先级越高；
- intercept 只影响配置解析，不影响服务查找与依赖响应。
'''

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cordis_py import Context, Loader, Service, inject


def test_intercept_merge_leaf_wins() -> None:
    '''祖先条目先应用，叶子条目覆盖祖先。'''
    root = Context()
    seen: list[Any] = []

    class Model(Service):
        def __init__(self, ctx: Context) -> None:
            super().__init__(ctx, "model")
            seen.append(ctx.intercept_config("model"))

    scoped = root.intercept("model", {"a": 1, "b": 2}).intercept("model", {"b": 3})
    scoped.plugin(Model)
    assert seen == [{"a": 1, "b": 3}]
    root.fiber.dispose_sync()


def test_intercept_scoped_to_child() -> None:
    '''拦截条目只影响其所在分支，根上下文与其他分支互不干扰。'''
    root = Context()
    assert root.intercept_config("model") is None
    child = root.intercept("model", {"a": 1})
    other = root.intercept("model", {"b": 2})
    assert child.intercept_config("model") == {"a": 1}
    assert other.intercept_config("model") == {"b": 2}


def test_intercept_absent_returns_none() -> None:
    '''没有拦截条目时返回 None；无关条目不影响。'''
    root = Context()
    assert root.intercept_config("model") is None
    root.intercept("other", {"x": 1})
    assert root.intercept_config("model") is None


def test_intercept_non_mapping_replaces() -> None:
    '''非映射条目整体替换，叶子非映射覆盖祖先映射；映射条目按浅合并。'''
    root = Context()
    assert root.intercept("model", 5).intercept_config("model") == 5
    chain = root.intercept("model", {"a": 1}).intercept("model", {"b": 2})
    assert chain.intercept_config("model") == {"a": 1, "b": 2}
    chain2 = root.intercept("model", {"a": 1}).intercept("model", 5)
    assert chain2.intercept_config("model") == 5


def test_inject_config_enters_intercept_chain() -> None:
    '''插件 inject 声明中的非空配置进入其上下文拦截链。'''
    root = Context()

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("model", object())

    root.plugin(provider)
    seen: list[Any] = []

    @inject({"model": {"retries": 3}})
    def consumer(ctx: Context, config: dict) -> None:
        seen.append(ctx.intercept_config("model"))

    root.plugin(consumer)
    assert seen == [{"retries": 3}]
    root.fiber.dispose_sync()


def test_intercept_does_not_change_dependency_resolution() -> None:
    '''intercept 不改变服务查找：子上下文仍能看到父作用域的服务。'''
    root = Context()

    def provider(ctx: Context, config: dict) -> None:
        ctx.provide("model", {"ok": True})

    root.plugin(provider)
    seen: list[Any] = []

    @inject("model")
    def consumer(ctx: Context, config: dict) -> None:
        seen.append(ctx.model)

    root.intercept("model", {"decorate": False}).plugin(consumer)
    assert seen == [{"ok": True}]
    root.fiber.dispose_sync()


def test_service_resolve_config_base_head() -> None:
    '''resolve_config 合并 base（最低优先级）与 head（最高优先级）配置。'''
    root = Context()
    seen: list[Any] = []

    class Model(Service):
        def __init__(self, ctx: Context) -> None:
            super().__init__(ctx, "model")
            seen.append(self.resolve_config({"from": "base"}, {"from": "head", "extra": True}))

    root.intercept("model", {"from": "intercept", "extra": False}).plugin(Model)
    assert seen == [{"from": "head", "extra": True}]
    root.fiber.dispose_sync()


async def test_loader_entry_intercept_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''Loader 条目上的 intercept 配置对其插件可见。'''
    module = tmp_path / "intercept_plugins.py"
    module.write_text(
        """
from cordis_py import Context

def scoped(ctx: Context, config: dict):
    ctx.provide("scoped", ctx.intercept_config("model"))
    return None
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    root = Context()
    loader = Loader(root)
    await loader.reconcile(
        [
            {
                "id": "scoped",
                "url": "intercept_plugins:scoped",
                "intercept": {"model": {"tenant": "a"}},
            }
        ]
    )
    assert root.services["scoped"] == {"tenant": "a"}
    await loader.dispose()
    await root.fiber.dispose()
