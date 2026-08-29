"""版本契约测试：@require 的多种约束形态（PEP 440 specifier / 谓词）。

tags 插件提供带版本（1.0）的 tags 服务；本文件验证消费方契约：
- 精确锁定 ``==1.0``、``~=1.0``、范围 ``>=1.0,<2.0`` 均满足 → 激活；
- 超出范围（``>=2.0``）→ 软等待（PENDING），不构成错误；
- 谓词约束（如 ``hasattr(svc, "list")``）→ 满足。
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from cordis_py import Context, Loader, inject, require


def _plugin_with_requirement(constraint):
    """生成一个只依赖 tags 服务的消费者插件（带指定约束）。"""

    @inject(["tags"])
    @require("tags", constraint)
    def consumer(ctx: Context, config) -> None:
        ctx.provide("consumer", True)

    return consumer


async def _compose(constraint) -> tuple[Context, Loader]:
    root = Context()
    loader = Loader(root)
    root.provide("fastapi_app", FastAPI())
    await loader.reconcile([
        {"id": "logger", "url": "plugins.logger_plugin:plugin", "config": {}},
        {"id": "tenant", "url": "plugins.tenant:plugin", "config": {"tenants": ["acme"]}},
        {"id": "http", "url": "plugins.http_service:plugin", "config": {}},
        {"id": "tags", "url": "plugins.tags:plugin", "config": {}},
    ])
    # 消费者插件直接注册到 root（响应式：tags 已激活 → 立即满足/软等待）。
    await root.plugin(_plugin_with_requirement(constraint))
    return root, loader


async def test_version_exact_match_activates() -> None:
    root, loader = await _compose("==1.0")
    try:
        # 精确锁定 1.0：tags 提供 version=1.0 → 满足 → consumer 激活。
        assert root.services.get("consumer") is True
    finally:
        await loader.dispose()
        await root.fiber.dispose()


async def test_version_range_match_activates() -> None:
    root, loader = await _compose(">=1.0,<2.0")
    try:
        assert root.services.get("consumer") is True
    finally:
        await loader.dispose()
        await root.fiber.dispose()


async def test_version_too_high_soft_waits() -> None:
    root, loader = await _compose(">=2.0")
    try:
        # 软等待：不报错、无服务，fiber 停留在 PENDING 且可诊断。
        assert root.services.get("consumer") is None
        fibers = [f for f in root._fibers if getattr(f.plugin, "__name__", "") == "consumer"]
        assert fibers and fibers[0].state.value == "pending"
        assert "tags" in fibers[0].unsatisfied
    finally:
        await loader.dispose()
        await root.fiber.dispose()


async def test_predicate_constraint_activates() -> None:
    root, loader = await _compose(lambda svc: hasattr(svc, "list"))
    try:
        assert root.services.get("consumer") is True
    finally:
        await loader.dispose()
        await root.fiber.dispose()


def test_tags_service_has_version() -> None:
    """tags 插件提供带版本号的服务（契约可被 require 感知）。"""
    root = Context()
    loader = Loader(root)
    root.provide("fastapi_app", FastAPI())

    async def check() -> None:
        await loader.reconcile([
            {"id": "logger", "url": "plugins.logger_plugin:plugin", "config": {}},
            {"id": "tenant", "url": "plugins.tenant:plugin", "config": {"tenants": ["acme"]}},
            {"id": "http", "url": "plugins.http_service:plugin", "config": {}},
            {"id": "tags", "url": "plugins.tags:plugin", "config": {}},
        ])
        entry = next(
            e for e in root._services.values() if e.name == "tags"
        )
        assert entry.version == "1.0"

    asyncio.run(check())
