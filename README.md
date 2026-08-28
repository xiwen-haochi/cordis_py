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
pip install cordis-python            # PyPI 发布名；导入名 cordis_py
pip install cordis-python[watch]     # 附加 HMR 文件监听（watchdog）
pip install cordis-python[yaml,pydantic]  # 附加 YAML 配置 / 配置模型校验
```

---

## 教程：三个实战场景

### 场景 A：插件化 HTTP 多租户任务 API（生产案例）

完整代码在 [examples/task_api](examples/task_api/README.md)：FastAPI 应用由 **9 个插件声明式装配**，`app.yml` 就是架构图；徒手写一个同规模应用通常要数百行 main.py。

**运行**

```bash
cd examples/task_api
pip install -r requirements.txt        # fastapi / uvicorn / httpx
python main.py --port 8000             # 启动即启用 HMR（默认开启，见 main 入口）
pytest tests/ -q                       # 6 个集成测试（TestClient 全链路）
```

**体验（多租户 + 限流 + 指标）**

```bash
curl -s http://127.0.0.1:8000/api/health                       # 公开健康检查
curl -s -X POST -H "X-Tenant: acme" -H "X-API-Key: key-acme" \
     -H "Content-Type: application/json" -d '{"title": "写文档", "priority": 2}' \
     http://127.0.0.1:8000/api/tasks                            # acme 创建
curl -s -H "X-Tenant: globex" -H "X-API-Key: key-globex" \
     http://127.0.0.1:8000/api/tasks                            # globex：空（数据隔离）
# 连续第 6 次请求（limit=5）→ 429 rate_limited
```

**每个插件的作用与配置参数**

| 插件 (id) | 职责 | 配置 | 说明 |
| --- | --- | --- | --- |
| `logger` | 提供 `log` 服务（标准库 logging 工厂） | `level`（默认 INFO） | 其他插件 `ctx.get("log")("name")` 取子 logger |
| `http` | 装配 FastAPI：gate 中间件（认证→瀑布→真实处理）、`routes` 路由注册表、`app` 服务 | `title` | 宿主注入 `fastapi_app`；路由可逆（重载自动卸载） |
| `tenant` | 为每个租户 `isolate("tasks", realm)` 并注册专属存储；提供 `tenants` 服务（version=1.0） | `tenants: [acme, globex]` | realm 服务解析：`scoped.get("tasks")` 物理隔离 |
| `auth` | X-Tenant / X-API-Key 校验 → `request.state.tenant` | `keys: {tenant: key}`、`public_paths` | 失败返回 401；公开路径免认证直通 |
| `quota` | 固定窗口限流，`http/request` 瀑布链短路返回 429 | `limit`（默认 5）、`window` 秒（默认 30） | 不调用 `next()` 即拦截；公开路径不限流 |
| `tasks` | 任务 CRUD + 事件（task/created、deleted）+ 指标 | `page_size`（默认 20） | **依赖 routes/tenants，装配顺序无关**（响应式） |
| `audit` | 监听任务事件写审计日志 | `event_prefix` | 随插件 fiber 回收 |
| `metrics` | 计数服务 + `/api/metrics` 路由 | `namespace`（默认 cordis） | 键形如 `taskapi.tasks.created` |
| `health` | `/api/health`（公开：服务状态/租户列表） | — | — |

**三个关键机制**

- **响应式装配**：`tasks` 在配置中排在 `http` / `tenant` 之前——因依赖缺失软等待，提供者出现后自动激活并注册路由；
- **瀑布中间件**：每请求 `ctx.waterfall("http/request", tenant, request, fallback=call_next)`，插件式拦截（限流/校验），链尾是真实处理；
- **可逆路由**：业务插件 `ctx.effect` 登记路由 disposer——插件卸载时路由自动移除，HMR 重载时整体替换。

---

### 场景 B：开发期热更新（HMR）

**目的**：改插件源码不需要重启进程——插件热替换、依赖树精确分类、失败自动回滚。

**最小用法**

```python
from cordis_py import Context, HMR, Loader

root = Context()
loader = Loader(root)
await loader.include("app.yml")            # 声明式装配

hmr = HMR(loader)                          # 安装模块依赖图追踪（运行时 + AST 补全）
watcher = hmr.watch(["src/plugins"])       # 监听源码目录（需要 cordis-python[watch]）

await hmr.reload_file("src/plugins/worker.py")   # 手动触发：变更文件 → 事务式重载
await hmr.reload_module("myapp.helpers")         # 或按模块名
await hmr.reload_entry("worker")                 # 或按条目 id（连带重载其依赖者）

await watcher.stop()
hmr.dispose()
```

**验证热替换**：修改 `src/plugins/worker.py`（如切换模型服务、调整阈值）并保存——
1.5 秒内新逻辑生效：旧 fiber 被 dispose（服务/监听器/路由/内部状态整体清理），新代码重新应用；
限流窗口、计数器等插件内部状态归零即证明“旧实例已被替换”。支持编辑器原子保存（临时文件 + rename）。

**API 与参数**

| API | 参数 | 说明 |
| --- | --- | --- |
| `HMR(loader, *, graph=None)` | `graph`：共享/替换依赖图实例 | 构造即安装导入边追踪器 |
| `watch(roots, *, ignored, debounce, recursive, backend, on_error)` | `roots` 目录列表；`ignored` 默认 `("**/.*","**/__pycache__","**/node_modules","**/.venv","cache","data")`；`debounce` 秒（默认 0.1）；`on_error(path, exc)` 失败回调 | 返回 `HMRWatcher`；事件线程安全 |
| `reload_file(path)` | 文件路径（未导入/非 `.py` 自然忽略） | 返回受影响条目 id 列表 |
| `reload_module(name)` / `reload_entry(id)` / `reload_all()` | 模块名 / 条目 id / 全量 | 同上；entry 会连带重载其依赖者 |
| `affected(changed)` | 变更模块集合 | 只预测不执行 |
| `dispose()` / `watcher.stop()` | — | 卸载追踪器 / 停止监听（幂等） |

**机制**：变更模块按依赖图分类为 accepted/declined（对齐 Node `analyzeChanges`）；重载集 = 变更模块 + 受影响条目模块（按拓扑序，依赖先于导入者）；失败时恢复模块命名空间快照并用旧插件重新应用（回滚）。

---

### 场景 C：跨进程共享服务（Bridge）

**目的**：昂贵的共享能力（模型、缓存客户端、数据库连接池）留在主进程，worker 进程按需代理；服务与事件语义跨进程保持一致。

**服务端（主进程：暴露服务）**

```python
from cordis_py import Bridge, Context

server, addr = await Bridge.serve()                 # TCP 默认 127.0.0.1:0
server.expose(root, "model", ModelService(), version="1.0")   # 可逆注册
```

**客户端（worker：代理调用 + 事件）**

```python
client = await Bridge.connect(addr)                 # "host:port" 或 Unix socket 路径
result = await client.proxy("model").predict(payload)   # 异步远程调用
client.send_event("model/updated", "v2")                # 事件贯通（fire-and-forget）
disposer = client.on_event("train/event", handler)      # 接收对端事件
await client.close()                                    # 幂等：bye → 未决调用失败
```

**边界与参数**

| API / 语义 | 参数 | 说明 |
| --- | --- | --- |
| `Bridge.serve(host, port, unix, name)` | `unix` 给定则为 Unix socket 监听；返回 `(bridge, address)` | 单连接（MVP） |
| `Bridge.connect(address, name)` | `host:port` 或 Unix 路径（含 `/` 判定） | 连接后 hello 握手（protocol=1，5s 超时） |
| `expose(ctx, name, service, *, version)` | 服务对象；`version` 供契约 | 返回 Fiber；dispose 自动反注册；重复暴露抛 `ServiceConflict` |
| `proxy(name)` | 服务名 | `await proxy.method(*args)`；可调用对象直接 `await proxy(*args)` |
| `send_event / on_event` | 事件名 + 参数 | 仅 JSON 兼容值；断连时 `send_event` 静默、调用抛 `RemoteClosed` |
| 错误 | — | 远端异常重建 `RemoteError`（类型名/消息）；协议损坏抛 `ProtocolError`；同步调用链抛 `AsyncRequiredError` |

---

## 核心 API 速查

| 想做什么 | 用什么 | 一句话说明 |
| --- | --- | --- |
| 能力可插拔、可拆除 | `ctx.provide()` / `ctx.effect()` / `Fiber.dispose()` | 可逆副作用是全部语义的底座 |
| 声明式装配应用 | `Loader.include("app.yml")` → `reconcile / disable / enable` | 配置文件即架构图（JSON/YAML/TOML） |
| 依赖升级护栏 | `Service.version` + `@require("svc", ">=1.0")` | 版本/接口契约，不满足=软等待 |
| 中间件 / 拦截链 | `ctx.on(event, handler)` + `waterfall(..., fallback=...)` | 不调用 `next()` 即拦截（限流/鉴权） |
| 多租户 | `ctx.isolate(name, realm)` + `internal/config` overlay | realm 服务隔离 + 租户配置派生 |
| 不可信插件边界 | `Context.filtered()` / `create_scope` / `scope_target` | 事件协调式可见性隔离（非安全沙箱） |
| 跨进程服务 | `Bridge.serve/connect` + `expose/proxy` | 远程服务代理 + 事件贯通 |
| 开发期热替换 | `HMR(loader)` + `watch([...])` | 依赖图分类 + 事务式重载，失败回滚 |
| 插件自动发现 | `discover()` / `load_entry_points()` | Python 包入口点（插件市场形态） |

## 质量与边界

- **验证**：100+ 单元/集成测试（生命周期、事件、契约、HMR 依赖图、作用域路由、Bridge 协议、watcher 原子保存）；ruff 全绿；`py.typed` 随包发布；生产案例带 6 个 e2e 集成测试与实测 curl 序列。
- **可选依赖**：PyYAML / watch / pydantic 均为 extra，核心零第三方运行时依赖（仅 `packaging` 用于版本约束）。
- **诚实边界**：作用域隔离是协调式（事件/服务可见性），**不是**恶意代码的安全边界（OS 级沙箱属宿主职责）；Bridge 无认证/加密层，限受信内网；跨进程调用仅 JSON 兼容值；HMR 仅纯 Python 源码模块。
- **版本**：`0.9.0`；0.x 阶段 API 按语义化演进，1.0 前事项：对外契约类型化（mypy）、CI、并发不变量压测。

## 参考

- 论文：*A Programming Paradigm for Spatiotemporal Composability*
- Node.js 版：Cordis v4 / DeepSeek Harness vendor（本实现完全以其语义为基准，不参考其他 Python Cordis 实现）
