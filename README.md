# cordis_py

**Cordis 的 Python 实现**：面向动态系统的时空可组合性（spatiotemporal composability）元框架——把“装上的东西都能拆下来、依赖变化自动联动、能力可以声明式组合”变成可用的 Python 动态组合框架。

## 原理（30 秒版）

```text
                ┌────────────────────────────────────────────┐
  宿主        │  Context（根容器）                          │
 (main.py)    │   ├── Fiber：插件运行实例 + 生命周期状态机    │
   │          │   ├── 服务：provide / get / inject（realm 隔离）│
   │          │   ├── 事件：on / emit / parallel / waterfall │
   │          │   └── 效果：一切注册都可逆（LIFO 回收）       │
   │ 注入/装配  └──────────────┬─────────────────────────────┘
   ▼                           │ 声明式装配
  Loader ── 配置文件(JSON/YAML/TOML) ──> 插件列表（url + config）
                     │
                     ▼
               每个插件 = 一段代码 + 一份配置：
               - 提供服务（供别的插件消费）
               - 监听事件 / 组装中间件链（waterfall）
               - 注册路由、资源等一切“装上去”的东西
```

三句话讲清楚：

1. **应用 = 插件集合**：能力（服务、路由、中间件、任务）都是插件；`Loader` 从一份配置文件声明式装配，新增能力 = 加一行配置。
2. **一切可逆**：插件运行在 Fiber 里，注册的一切（服务、事件监听、路由、定时任务）随 Fiber 卸载自动回收（LIFO）——不会“改了一半”的应用状态。
3. **依赖响应式**：先装消费者后装提供者也能自动激活；提供者卸载后消费者自动退出。装配顺序无关。

## 安装

```bash
pip install cordis-python          # PyPI 发布名；导入名 cordis_py
pip install cordis-python[watch]   # 附加 HMR 文件监听（watchdog）
```

## 快速开始

```python
import asyncio
from cordis_py import Context, Service, inject


class Greeter(Service):                 # 1. 服务：能力的最小单元
    def __init__(self, ctx: Context):
        super().__init__(ctx, "greeter")

    def hello(self, name: str) -> str:
        return f"Hello, {name}!"


@inject("greeter")                      # 2. 插件：声明依赖的消费者
def greeter_plugin(ctx: Context, config: dict):
    ctx.on("app/ready", lambda msg: print(ctx.greeter.hello(msg)))
    return None


async def main():
    root = Context()                    # 3. 根容器
    await root.plugin(Greeter)          #    装服务（可逆）
    await root.plugin(greeter_plugin)   #    装插件（可逆；顺序颠倒也能自动激活）
    root.emit("app/ready", "Cordis")    # 4. 事件
    await root.fiber.dispose()          # 5. 全部回收（LIFO，无残留）


asyncio.run(main())
```

没有事件循环时用同步模式：`root.fiber.dispose_sync()`（其余转换用 `restart_sync` / `update_sync`；需要事件循环的操作会抛 `AsyncRequiredError`）。

## 能力速查

| 想做什么 | 用什么 | 说明 |
| --- | --- | --- |
| 能力可插拔、可拆除 | `ctx.provide()` / `ctx.effect()` / `Fiber.dispose()` | 可逆副作用是全部语义的底座 |
| 声明式装配应用 | `Loader.include("app.yml")` → `reconcile / disable / enable` | 配置文件即架构图 |
| 依赖升级护栏 | `Service.version` + `@require("svc", ">=1.0")` | 版本/接口契约，不满足=软等待 |
| 中间件 / 拦截链 | `ctx.on("http/request", handler)` + `waterfall(..., fallback=call_next)` | 不调用 `next()` 即拦截（限流/鉴权） |
| 多租户 | `ctx.isolate("tasks", tenant)` + `internal/config` overlay | realm 服务隔离 + 租户配置派生 |
| 不可信插件边界 | `Context.filtered()` / `create_scope` / `scope_target` | 事件的协调式可见性隔离 |
| 跨进程服务 | `Bridge.serve/connect` + `expose/proxy` | JSON-lines 帧协议，事件双向贯通 |
| 开发期热替换 | `HMR(loader)` + `watch([...])` | 依赖图分类 + 事务式重载，失败自动回滚 |
| 插件自动发现 | `discover()` / `load_entry_points()` | Python 包入口点（插件市场形态） |

## 生产案例

[examples/task_api](examples/task_api/README.md) —— **插件化多租户任务 API（FastAPI）**：9 个插件、`app.yml` 声明装配、请求瀑布链中间件、per-realm 租户数据隔离、契约校验、限流/审计/指标、HMR 热替换（改插件源码不重启进程，支持编辑器原子保存）。带集成测试与实测 curl 序列。

## 质量与边界

- **验证**：100+ 单元/集成测试（生命周期、事件、契约、HMR 依赖图、作用域路由、Bridge 协议、watcher 原子保存）；ruff 全绿；`py.typed` 随包发布。
- **可选依赖**：PyYAML / watch / pydantic 均为 extra，核心零第三方运行时依赖（仅 `packaging` 用于版本约束）。
- **诚实边界**：作用域隔离是协调式（事件/服务可见性），**不是**恶意代码的安全边界（OS 级沙箱属宿主职责）；Bridge 无认证/加密层，限受信内网；跨进程调用仅 JSON 兼容值；HMR 仅纯 Python 源码模块。
- **版本**：0.x 阶段，API 按语义化演进；重大变更会在 changelog 说明（当前 `0.9.0`）。

## 参考

- 论文：*A Programming Paradigm for Spatiotemporal Composability*
- Node.js 版：Cordis v4 / DeepSeek Harness vendor（本实现完全以其语义为基准，不参考其他 Python Cordis 实现）
