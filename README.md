# cordis_py

Cordis 的 Python 实现：面向动态系统的时空可组合性（spatiotemporal composability）元框架。

## 当前已实现

- **Context / Fiber**：插件运行时实例、生命周期状态机、依赖驱动的自动加载/卸载。
- **Effect 追踪**：`ctx.effect()` 支持 disposer、同步/异步 iterable，卸载时 LIFO 清理。
- **服务与依赖注入**：`ctx.provide()`、`ctx.get()`、`ctx.set()`、`inject` 声明。
- **响应式依赖**：先加载消费者再加载提供者也能自动激活；提供者卸载后消费者自动退出。
- **事件系统**：`on` / `once` / `emit` / `parallel` / `serial` / `bail` / `waterfall`。
- **Service 基类**：继承 `Service` 并调用 `super().__init__(ctx, name)` 自动注册服务。
- **Per-realm 隔离**：`ctx.isolate(name, realm)` 可隔离同名服务。
- **Intercept 配置拦截**：`ctx.intercept(name, config)` 沿上下文链合并服务级配置（祖先条目先应用、就近覆盖），插件 inject 声明中的非空配置自动并入，`Service.resolve_config()` 可读取合并结果。
- **契约校验**：`Service.version` / `ctx.provide(version=)` 声明版本，`@require` 声明 PEP 440 版本约束或接口谓词（不满足时软等待，`fiber.unsatisfied` 可诊断）；插件 `Config` 属性支持 callable 与 pydantic（可选）配置校验。
- **配置 overlay**：`internal/config` waterfall——插件 config 激活前经父链监听器改写（只对注册者的后代生效），配合 `deep_merge` 实现分层合并与租户派生；改写结果再进入 `Config` 校验。
- **同步/异步双模式**：有运行事件循环时后台异步调度；无事件循环时生命周期内联驱动（`dispose_sync` / `restart_sync` / `update_sync`），遇到需要事件循环的操作抛出 `AsyncRequiredError`。
- **声明式 Loader**：支持 JSON/YAML/TOML 配置、增量 reconcile、disable/enable。
- **基础 HMR**：开发期针对单个 Loader Entry 的模块重载。

## 安装

PyPI 发布名为 `cordis-python`，Python 导入名仍为 `cordis_py`：

```bash
pip install cordis-python
```

## 快速开始

```python
import asyncio
from cordis_py import Context, Service, inject


class Greeter(Service):
    def __init__(self, ctx: Context):
        super().__init__(ctx, "greeter")

    def hello(self, name: str) -> str:
        return f"Hello, {name}!"


@inject("greeter")
def greeter_plugin(ctx: Context, config: dict):
    ctx.on("app/ready", lambda msg: print(ctx.greeter.hello(msg)))
    return None


async def main():
    root = Context()
    await root.plugin(Greeter)
    await root.plugin(greeter_plugin)

    root.emit("app/ready", "Cordis")
    await root.fiber.dispose()


asyncio.run(main())
```

### 同步模式

没有运行事件循环时，生命周期转换会内联完成，配合 `dispose_sync` 等同步 API 使用：

```python
from cordis_py import Context, inject


@inject("greeter")
def greet_plugin(ctx: Context, config: dict):
    print(ctx.greeter.hello("world"))


root = Context()
root.plugin(Greeter)
root.plugin(greet_plugin)
root.fiber.dispose_sync()
```

若同步调用链中遇到需要事件循环的操作（异步插件、异步效果、异步事件监听器），会抛出 `AsyncRequiredError`，提示改用异步 API。

### 契约校验

提供方声明版本，消费方用 `@require` 声明约束；约束不满足时消费者保持等待（软等待），提供方变化后自动重新评估：

```python
from cordis_py import Context, Service, inject, require


class Model(Service):
    version = "1.0.0"

    def __init__(self, ctx):
        super().__init__(ctx, "model")


@inject("model")
@require("model", ">=1.0,<2.0")                    # PEP 440 版本约束
@require("model", lambda svc: hasattr(svc, "hello"))  # 接口谓词
def consumer(ctx: Context, config: dict):
    print(ctx.model)
```

配置校验：给插件挂 `Config` 属性（callable 校验/转换，或可选安装 pydantic 后用模型类）。

### 配置 overlay / 租户派生

插件配置在激活前经过 `internal/config` 瀑布链（先改写、后 `Config` 校验），监听器只对注册者的后代生效：

```python
from cordis_py import Context, deep_merge


async def tenant_overlay(fiber, config, next):
    tenant = fiber.ctx._isolation.get("tenant")   # 目标 fiber 的上下文标记
    return deep_merge(await next(), {"tenant": tenant})


root = Context()
root.on("internal/config", tenant_overlay)        # 对后代插件全体生效
root.plugin(some_plugin)                          # 其 config 自动叠加租户层
```

## 文档

- [开发流程](DEVELOPMENT.md)
- [介绍与实现思路（HTML）](docs/cordis_py_intro.html)
- [应用领域与设计优化分析（HTML）](docs/cordis_py_domains_and_design.html)

## 参考

- 论文：*A Programming Paradigm for Spatiotemporal Composability*
- Node.js 版：Cordis v4 / DeepSeek Harness vendor
