# cordis_py 收官总结：从论文语义到可运行元框架

> 对应版本：0.8.0（2026-08-28）
> 参考依据：论文《A Programming Paradigm for Spatiotemporal Composability》与 Node.js 版 Cordis（Cordis v4 / DeepSeek Harness vendor），未参考任何现有 Python Cordis 实现包。

---

## 1. 定位：元框架而非框架

cordis_py 提供的是**组合语义**，不是任何具体应用形态：

- 不内置 Web 服务器（对应 Node Cordis 不内置 express；koishi 是它的具体化例子）；
- 不内置数据库、任务队列、训练循环；
- 它交付的是四条不变式，任何应用都建立在它们之上：

1. **可逆副作用**：一切“装上的东西”都能按 LIFO 拆下来，且不残留。
2. **响应式依赖**：先装消费者后装提供者也能自动激活；提供者卸载后消费者自动退出。
3. **可组合的三层边界**：服务（realm 隔离 / 契约校验 / 配置 overlay）、事件（五种模式 / 监听器过滤）、装配（声明式 Loader / 增量 reconcile）。
4. **运行时可观测、可替换**：fiber 生命周期状态机公开可诊断；开发期 HMR 事务式替换源码；跨进程 Bridge 扩展边界。

## 2. 完成度：九个功能点与版本演化

| 版本 | 功能点 | 核心交付 |
| --- | --- | --- |
| 0.1.0 | 核心运行时 | Context/Fiber、可逆效果、响应式依赖、事件系统、Service 基类、per-realm 隔离、插件发现 |
| 0.2.0 | 同步/异步双模式 + intercept | `has_running_loop()` 路由、`dispose_sync/restart_sync/update_sync`、服务级配置拦截 |
| 0.3.0 | 契约校验 | `Service.version`、`@require`（PEP 440 约束 + 接口谓词、软等待）、插件 `Config` 校验 |
| 0.4.0 | 配置 overlay / 租户派生 | `internal/config` 瀑布链、`deep_merge`、父链监听器作用域 |
| 0.5.0 | HMR 依赖图分类 | 模块边追踪（运行时 + AST 补全）、accepted/declined 分类、事务式重载与回滚 |
| 0.6.0 | HMR 文件监听器 | watchdog 后端、debounce 合并、错误回调、`HMR.watch()` |
| 0.7.0 | 作用域隔离 | `Context.filter` 监听器过滤、`create_scope/scope_target` 路由（事件只向上流） |
| 0.8.0 | 跨进程 Bridge / 远程服务 | JSON-lines 帧协议、`expose/proxy`、事件贯通、`RemoteError/RemoteClosed` |

每项均配套：设计文档（`docs/specs/`）、实现计划（`docs/plans/`）、测试、两份 HTML 文档 TODO 更新、中文提交。

## 3. 核心设计的取舍与差异说明

忠实移植之外，有两处**刻意的 Python 化差异**，均写入对应设计文档：

- **HMR 重载集**：Node 会把条目依赖闭包并入 accepted（为 ESM 缓存失效服务）；Python 的 `importlib.reload` 需要显式指定，重载集 = 变更模块 + 受影响条目模块，未变更依赖保持缓存——避免无谓重执行模块级副作用与共享依赖误伤。
- **作用域路由方向**：事件只向上流（祖先作用域可观察后代事件，反之不可），与 harness scope 包语义一致；`global_` 监听器是显式放行口。

其余语义（隔离、intercept、契约、分类算法）与 Node Cordis v4 保持对齐，测试覆盖含 `analyzeChanges` 的逐轮分类、断开竞态、取消路径关闭等真实边界。

## 4. 诚实的边界

- **HMR**：仅纯 Python 源码模块可重载（扩展模块跳过）；追踪器安装前的**动态**导入边缺失（静态由 AST 补全）；`__main__` 或 cordis_py 自身变更 = 全量重启。
- **作用域隔离**：是**协调式**边界（事件/服务可见性），不是恶意代码的真实安全边界——Python 无真沙箱，`import os` 仍可逃逸，OS 级资源沙箱属宿主职责。
- **Bridge**：无认证/加密层（仅内网/受信场景），仅 JSON 兼容值，跨进程调用必然是异步 IO。
- 配置层依赖 `packaging`（版本约束）；`pydantic` / `PyYAML` / `watchdog` 均为可选 extra。

## 5. 生态结合的展望（示例级即可落地）

| 生态位 | 结合方式 |
| --- | --- |
| FastAPI / Starlette | lifespan 启动/关闭 `Context`；`Depends` 桥接 `ctx.get`；路由注册事件；请求瀑布 = 中间件（不调用 `next` 即拦截返回） |
| 多 worker 部署 | Bridge：共享服务（模型/缓存客户端）放主进程，worker `proxy()` 调用 |
| 多租户 SaaS | `isolate`（每请求 realm 服务栈）+ `internal/config` overlay（租户配置派生）—— 0.4.0/0.7.0 已备好原语 |
| 开发体验 | `HMR.watch()` 插件热替换不重启进程；CLI（`cordis-py run`）是后续封装方向 |
| Agent / Bot / 数据管道 | 算子/技能以插件提供，热更新单算子而不重启整条 Pipeline |

## 6. 路线图（按优先级）

- **P0 生态验证**：FastAPI 适配层（lifespan + Depends + 路由事件 + 请求瀑布）；CLI `cordis-py run/init`（发现 + 加载 + watch + 信号处理）。
- **P1 质量**：mypy 全绿 + `py.typed` 确认 + mkdocs API 参考（当前对外契约 `Any` 偏多）；fiber 状态机并发不变量压力测试；配置校验路径上下文与 `fiber.unsatisfied` 诊断强化。
- **P2 能力面**：日志/指标/追踪插件（事件流观测）、timer 插件（对应 Node `cordis-plugin-timer`）、远程 Loader（Bridge 上暴露条目）、Bridge 安全层。
- **P3 可维护**：文档站；示例库（fastapi / CLI / bot / 多租户四场景）；GitHub Actions（pytest + ruff + build + 发布自动化）。

## 7. 用最短路径上手

```python
from cordis_py import Context, Loader

root = Context()
loader = Loader(root)
await loader.include("app.yml")     # 声明式装配：插件 = 配置 + 代码
...
await loader.dispose()              # 全部副作用可逆回收
```

- 开发流程与约定：`DEVELOPMENT.md`；
- 呈现层文档：`docs/cordis_py_intro.html`、`docs/cordis_py_domains_and_design.html`；
- 发布：`uv build` + `uv publish`（0.8.0 已发布至 PyPI `cordis-python`）。
