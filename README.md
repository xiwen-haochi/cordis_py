# cordis_py

**Cordis 的 Python 实现**：面向动态系统的时空可组合性（spatiotemporal composability）元框架。

```python
pip install cordis-python
```

PyPI 发布名为 `cordis-python`（导入名 `cordis_py`），仓库：<https://github.com/xiwen-haochi/cordis_py>。

---

## 1. 为什么存在（三个痛点 → 三个承诺）

| 传统动态系统的痛点                        | cordis_py 的承诺                                                                         |
| ----------------------------------------- | ---------------------------------------------------------------------------------------- |
| 功能“装上去容易、拆下来难”                | **一切可逆**：卸载插件 = 自动回收它注册的所有东西（服务、事件、路由、资源），无残留      |
| 组件初始化顺序写死，换顺序/加组件就是重构 | **依赖响应式**：先装消费者后装提供者也能自动激活；装配顺序无关                           |
| 能力都在主程序里，扩展 = 改核心代码       | **声明式组合**：应用 = 配置（插件列表）+ 插件（代码）；新增能力 = 加一行配置、装一个插件 |

设计基准：论文《A Programming Paradigm for Spatiotemporal Composability》与 Node.js 版 Cordis v4。

## 2. 核心概念（30 秒总览）

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

### 2.1 概念详解

| 概念               | 是什么                                                                          | 为什么                                                                                                                              | 怎么用                                                                             |
| ------------------ | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **Context**        | 依赖容器（根或子）。插件拿到的每个 `ctx` 都是根链上的一个节点                   | 隔离与服务查找都发生在上下文链上（`isolate` / `intercept` / `filtered` 返回子上下文）                                               | `root = Context()`；插件函数第一参数                                               |
| **Fiber**          | 一个插件的运行实例，带生命周期状态机（PENDING → ACTIVE → UNLOADING → DISPOSED） | 状态公开可诊断（`fiber.state` / `fiber.unsatisfied`），可等待（`await fiber`）、可重启（`restart` / `update`）、可卸载（`dispose`） | `ctx.plugin(plugin, config)` 返回 Fiber                                            |
| **效果（Effect）** | 注册到一个 fiber 的可逆动作：`disposer` / 同步迭代 / 异步迭代                   | 可逆副作用的账本；fiber 卸载按 LIFO 逐个执行                                                                                        | `ctx.effect(lambda: undo)`；服务提供、事件监听、路由注册内部都登记为效果           |
| **服务与注入**     | `provide(name, value)` 注册；`ctx.get(name)` 读取；`@inject("name")` 声明消费   | 服务的可见性由“值的提供者是否 ACTIVE”决定——这是响应式依赖的载体                                                                     | `ctx.provide("db", db)`；`@inject(["db","log"]) def plugin(ctx, config)`           |
| **契约校验**       | `provide(version=)` + `@require("svc", ">=1.0")` / 接口谓词                     | 依赖升级的护栏；约束不满足 = 软等待，提供方变化后自动重评                                                                           | `@require("tenants", ">=1.0")`                                                     |
| **事件**           | `on` / `once` / `emit` / `parallel` / `serial` / `bail` / `waterfall`           | 插件间解耦通信；`waterfall` 是中间件链（不调用 `next()` 即拦截）                                                                    | `ctx.on("http/request", handler)`；监听器随插件 fiber 自动回收                     |
| **配置**           | 插件挂 `Config` 属性（callable 或 pydantic 模型，可选）校验/转换                | 配置错误在装载期报错，而不是运行期诡异行为                                                                                          | `plugin.Config = validate`；`internal/config` waterfall 可在激活前改写（租户派生） |
| **Loader**         | 从配置装配：`reconcile` 增量协调、`disable/enable`、JSON/YAML/TOML              | 应用状态与配置描述始终一致                                                                                                          | `Loader(root).include("app.yml")`                                                  |
| **HMR**            | 开发期热替换：模块依赖图分类 + 事务式重载 + 失败回滚                            | 改插件源码不重启进程（见场景 B）                                                                                                    | `HMR(loader)` + `watch(["src"])`                                                   |
| **隔离**           | `isolate`（服务 realm）/ `filtered` + scope（事件可见性）/ 契约                 | 多租户与不可信插件边界（协调式，非 OS 沙箱）                                                                                        | 见场景 A 与扩展实战                                                                |
| **Bridge**         | 跨进程服务代理与事件贯通（JSON-lines 帧协议）                                   | 昂贵共享能力留主进程，worker 代理调用                                                                                               | 见场景 C                                                                           |

---

## 3. 第一个插件（完整可运行：异步队列工作者）

展示四个核心行为：响应式注入（先装消费者）、服务、事件、**后台任务随卸载可逆取消**。
与场景 A/B/C 不重叠，且能直接跑：

```python
import asyncio
from cordis_py import Context, inject


def queue_provider(ctx: Context, config: dict):
    """插件一：提供服务 —— 一个异步队列（演示用）。"""
    ctx.provide("queue", asyncio.Queue())


@inject("queue")                                # 声明依赖：queue 未就绪时本插件不激活
def worker_plugin(ctx: Context, config: dict):
    """插件二：消费队列 —— 后台任务 + 事件发布；卸载时可逆取消。"""
    queue = ctx.queue                           # 激活后才可读（依赖已满足）

    async def consume():                        # 后台任务：随插件激活启动
        while True:
            item = await queue.get()
            result = {"item": item, "processed": True}
            ctx.emit("job/done", result)        # 事件：旁观者解耦监听

    task = asyncio.create_task(consume())

    def undo() -> None:
        task.cancel()                           # 卸载动作：撤销后台任务

    ctx.effect(lambda: undo)                    # 登记为可逆效果（fiber dispose 时执行）
    return None


async def main():
    root = Context()
    heard = []
    root.on("job/done", lambda result: heard.append(result["item"]))

    # 先装消费者（worker），后装提供者（queue）——顺序无关，自动激活
    await root.plugin(worker_plugin)            # 依赖未满足：软等待
    await root.plugin(queue_provider)           # 提供者出现 → worker 激活并开始消费
    await asyncio.sleep(0.05)                   # 允许后台激活完成

    root.services["queue"].put_nowait("task-1") # 投递任务
    await asyncio.sleep(0.05)
    assert heard == ["task-1"]                  # 消费链：queue → worker → 事件 → 旁听者

    await root.fiber.dispose()                  # LIFO 回收：消费任务被取消（可逆）


asyncio.run(main())
```

关键点：

- **`ctx.effect(lambda: undo)` 的写法是刻意的**：`effect` 会立即执行回调并收集返回值作为 disposer——所以必须返回`undo` 函数本身；写成 `ctx.effect(lambda: task.cancel())` 会在注册时立即取消任务并因返回值非法而报错；
- 服务/事件/任务全部登记为效果：`dispose()` 后应用无任何残留（打印、网络、定时器、监听器）。

没有事件循环时用同步 API：`root.fiber.dispose_sync()`（其余为 `restart_sync` / `update_sync`；遇到必须事件循环的操作会抛 `AsyncRequiredError`）。

---

## 4. 场景 A：插件化 HTTP 多租户任务 API（examples/task_api）

### 4.1 这个 example 为什么这么写（设计目的）

传统同规模 FastAPI 应用的主程序通常是：路由、中间件、依赖、配置、生命周期混在一个文件里。
example 的目的不是“演示功能”，而是展示**组织方式**：

- **每个关注点是一个插件**：HTTP 装配、认证、限流、审计、指标、业务、租户、健康检查是 8 个正交能力，各自一个文件 + 一份配置——增删能力不动其他文件；
- **配置即架构图**：`app.yml` 列出全部插件与其参数，读者不看 import 就能画出系统；
- **装配顺序刻意“错误”**：业务插件 `tasks` 排在 `http` / `tenant` 之前——证明依赖响应式（提供者后出现也能自动激活）；
- **宿主只做两件事**：创建应用对象（注入 `fastapi_app`）与加载配置——核心包零 Web 依赖，换框架只换宿主层；
- **一切可逆**：路由注册登记为效果——插件卸载/热替换时自动清理，不会出现“旧路由还在”的僵尸状态。

### 4.2 目录结构

```text
examples/task_api/
├── main.py                  # 宿主：约 40 行（创建 FastAPI、注入、装配、可选 HMR）
├── app.yml                  # 声明式装配：9 个插件
├── plugins/
│   ├── logger_plugin.py     # log 服务（日志工厂）
│   ├── http_service.py      # gate 中间件 + 瀑布链 + 路由注册表
│   ├── tenant.py            # 租户 realm 隔离 + TaskStore
│   ├── auth.py              # API Key 认证（契约：authenticate(request)）
│   ├── jwt_auth.py          # ← JWT 认证（同契约替代实现，见 §7）
│   ├── quota.py             # 限流（瀑布链短路径）
│   ├── audit.py             # 审计日志（事件监听）
│   ├── metrics.py           # 指标计数 + /api/metrics
│   ├── tasks.py             # 业务 CRUD（emit 事件 + 指标埋点）
│   └── health.py            # 健康检查
└── tests/                   # 11 个集成/单元测试（含 JWT 契约）
```

### 4.3 宿主：应用与插件唯一的接缝（main.py）

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    root = Context()
    root.provide("fastapi_app", app)        # ① 宿主注入运行时对象（不进配置文件）
    loader = Loader(root)
    await loader.include(BASE / "app.yml")  # ② 声明式装配（文件即架构）
    if getattr(app.state, "watch", False):  # ③ 开发期热替换（默认开启）
        hmr = HMR(loader)
        app.state.hmr_watcher = hmr.watch([str(BASE / "plugins")])
    yield
    await loader.dispose()                  # ④ 退出：reverse 顺序回收全部插件
    await root.fiber.dispose()
```

### 4.4 关键机制（三段代码）

**瀑布中间件**（http_service 插件）：认证 → 插件链 → 真实处理。`quota` 插件不调用 `next()` 就返回 429——中间件是“可插拔的插件”，不进 FastAPI 栈。

```python
async def gate(request, call_next):
    rejected = (ctx.get("auth") or NoopAuth()).authenticate(request)  # 认证是服务，可替换
    if rejected is not None:
        return rejected                                                # 401
    return await ctx.waterfall("http/request", tenant, request,
                               fallback=lambda *args: call_next(request))
```

**租户数据隔离**（tenant 插件）：`isolate("tasks", realm)` 为每个租户建立独立服务作用域，全局只按 `(realm, name)` 查找——globex 的请求**物理上**无法读到 acme 的存储（而不是靠业务里 `if tenant == ...` 的约定）。

```python
scoped = ctx.isolate("tasks", name)     # 子上下文：该租户 realm
scoped.provide("tasks", TaskStore(name))  # 键 = (name, "tasks")
...
store = self._scopes[name].get("tasks")   # 经该租户作用域解析
```

**可逆路由**（tasks 插件）：路由注册登记为 fiber 效果——插件重载时旧路由自动移除、新插件重注册同名路由（整体替换，无僵尸路由）。

```python
undos = [routes.add("GET", "/api/tasks", list_tasks), ...]
ctx.effect(lambda: undo_all)            # dispose 时逐一撤销
```

### 4.5 插件职责与配置参数

| 插件 (id) | 职责                                                       | 配置                                      | 说明                                     |
| --------- | ---------------------------------------------------------- | ----------------------------------------- | ---------------------------------------- |
| `logger`  | 提供 `log` 服务（logging 工厂）                            | `level`（默认 INFO）                      | 其他插件 `ctx.get("log")("name")`        |
| `http`    | 装配：gate 中间件、`routes` 注册表、`app` 服务             | `title`                                   | 宿主注入 `fastapi_app`                   |
| `tenant`  | 每租户 `isolate` + 专属存储；`tenants` 服务（version=1.0） | `tenants: [acme, globex]`                 | realm 服务查找                           |
| `auth`    | X-Tenant / X-API-Key → `request.state.tenant`              | `keys: {tenant: key}`、`public_paths`     | 失败 401；**契约接口可被 jwt_auth 替换** |
| `quota`   | 固定窗口限流（瀑布短路径 429）                             | `limit`（默认 5）、`window` 秒（默认 30） | 公开路径不限流                           |
| `tasks`   | 任务 CRUD + 事件 + 指标                                    | `page_size`（默认 20）                    | 装配顺序无关（响应式）                   |
| `audit`   | 任务事件审计日志                                           | `event_prefix`                            | 监听器随 fiber 回收                      |
| `metrics` | 计数服务 + `/api/metrics`                                  | `namespace`（默认 cordis）                | 键如 `taskapi.tasks.created`             |
| `health`  | 公开健康检查                                               | —                                         | 返回状态与租户列表                       |

### 4.6 运行与验证

```bash
cd examples/task_api
pip install -r requirements.txt     # fastapi / uvicorn / httpx / watchdog
python main.py --port 8000          # 启动即启用 HMR（默认开启）
pytest tests/ -q                    # 11 个测试
```

```bash
curl -s http://127.0.0.1:8000/api/health                                     # 公开健康检查
curl -s -X POST -H "X-Tenant: acme" -H "X-API-Key: key-acme" \
     -H "Content-Type: application/json" -d '{"title": "写文档", "priority": 2}' \
     http://127.0.0.1:8000/api/tasks                                          # acme 创建
curl -s -H "X-Tenant: globex" -H "X-API-Key: key-globex" \
     http://127.0.0.1:8000/api/tasks                                          # globex：空（隔离）
# 连续第 6 次请求 → 429 rate_limited；/api/metrics 查看计数
```

---

## 5. 场景 B：开发期热更新（HMR + 配置热更）

**目的**：改插件源码或装配配置不重启进程。依赖图分类保证“只重载受影响的条目”，事务回滚保证失败不破坏运行状态。配置热更走 Loader 增量协调（`fiber.update()`），与源码 HMR 并列。

```python
from cordis_py import HMR

hmr = HMR(loader)                          # 安装模块依赖图追踪（运行时导入边 + 已加载模块 AST 补全）
watcher = hmr.watch(["src/plugins"])       # 源码热替换（需要 cordis-python[watch]）
# watcher 参数：ignored（默认忽略 .*/__pycache__/node_modules/.venv/cache/data）、
#              debounce（秒，默认 0.1，合并连续保存）、recursive、on_error(path, exc)
config_watcher = hmr.watch_config(["app.yml"])  # 配置热更：改装配配置不重启
# watch_config 参数：targets（必须，配置文件路径）、debounce、backend、on_error

await hmr.reload_file("src/plugins/worker.py")   # 手动触发源码重载：返回受影响条目 id 列表
await hmr.reload_module("myapp.helpers")         # 或按模块名
await hmr.reload_entry("worker")                 # 或按条目 id（连带重载其依赖者）
await hmr.reload_config("app.yml")               # 手动触发配置重读：返回 config 变化的条目 id
print(hmr.affected({"myapp.helpers"}))           # 只预测不执行

await watcher.stop()
await config_watcher.stop()
hmr.dispose()                                    # 卸载追踪器（幂等）
```

**验证热替换**：修改 `src/plugins/worker.py` 保存后 1.5 秒内新逻辑生效；限流窗口、计数器等插件内部状态归零 = 旧实例已被完整替换。支持编辑器原子保存（临时文件 + rename，watcher 视为新建）。

**验证配置热更**：修改 `app.yml` 里某插件的 config 保存后，仅该条目被 `fiber.update()`（撤销旧效果、按新配置重启），其余插件不动；config 无变化的条目是空操作。注意：配置在启动时被 Loader 读入一次并定格在内存 `entry.config`，因此“改代码默认值”不是配置热更的生效路径——要么改配置（`reload_config`），要么改代码（`reload_file`），两条路径互不替代。

**机制**：变更模块按依赖图分类 accepted/declined（对齐 Node `analyzeChanges`）→ 重载集 = 变更模块 + 受影响条目模块（拓扑序，依赖先于导入者）→ 快照插件与模块命名空间 → 失败恢复快照并用旧插件重新应用（回滚）。仅纯 Python 源码模块；`__main__` / cordis_py 自身变更视为全量重启（不在范围）。

---

## 6. 场景 C：跨进程共享服务（Bridge）

**目的**：昂贵共享能力（模型、缓存客户端、连接池）留在主进程，worker 进程代理调用；服务与事件语义跨进程一致。

```python
# 主进程：暴露服务
from cordis_py import Bridge, Context
server, addr = await Bridge.serve()                          # TCP host:port 或 unix=套接字路径
server.expose(root, "model", ModelService(), version="1.0")  # 可逆：fiber dispose 自动反注册

# worker：代理调用 + 事件
client = await Bridge.connect(addr)                          # "host:port" 或 Unix 路径
result = await client.proxy("model").predict(payload)        # await proxy.method(*args)
client.send_event("model/updated", "v2")                     # 事件贯通（fire-and-forget）
disposer = client.on_event("train/event", handler)           # 接收对端事件（随 disposer 移除）
await client.close()                                         # 幂等：bye → 未决调用失败
```

**边界**：hello 握手（protocol=1，5s 超时，不兼容关闭）；仅 JSON 兼容值；远端异常重建 `RemoteError`（类型名/消息），断连后调用抛 `RemoteClosed`，协议损坏抛 `ProtocolError`；同步调用链抛 `AsyncRequiredError`；无认证/加密层，限受信内网。

---

## 7. 扩展实战：给应用加 JWT 认证

**思路**：认证在 cordis_py 里不是“内置功能”，而是**一个名为 `auth` 的服务契约**——`authenticate(request) -> None | JSONResponse`（成功写入 `request.state.tenant` 并返回 None，失败返回响应）。因此换认证方式 = 写一个实现同契约的插件，替换配置中的插件行。其余插件（http 的 gate、tasks、quota）**零改动**。

**第一步：编写插件（examples/task_api/plugins/jwt_auth.py，标准库 HS256）**

```python
class JwtAuthService:
    def __init__(self, secret, issuer="cordis-task-api", audience="cordis-task-api", public_paths=()):
        self._secret = secret.encode()
        ...

    def issue(self, tenant, *, ttl=3600) -> str:      # 签发（管理端/测试用）
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"sub": tenant, "iss": ..., "aud": ..., "exp": time.time() + ttl}
        signing = b".".join([_b64(json.dumps(header)), _b64(json.dumps(payload))])
        signature = hmac.new(self._secret, signing, hashlib.sha256).digest()
        return (signing + b"." + _b64(signature)).decode()

    def authenticate(self, request) -> JSONResponse | None:   # 契约入口
        if request.url.path in self._public_paths: return None
        token = request.headers.get("authorization", "")[7:]  # "Bearer xxx"
        # 验段数 → hmac.compare_digest 验签 → 校验 iss/aud/exp → request.state.tenant = sub
        ...

def plugin(ctx: Context, config: dict) -> None:
    ctx.provide("auth", JwtAuthService(secret=config["secret"], ...))   # 同名服务即可替换
```

**第二步：注册使用（app.yml 替换 auth 行——其余 8 个插件不动）**

```yaml
- id: auth
  url: plugins.jwt_auth:plugin # 原 plugins.auth:plugin
  config:
    secret: dev-secret-change-me # 生产用环境变量/密钥管理注入
    issuer: task-api
    audience: task-api
    public_paths: [/api/health] # 公开路径白名单
```

**第三步：验证**

```python
from plugins.jwt_auth import JwtAuthService
service = JwtAuthService(secret="dev-secret-change-me", issuer="task-api", audience="task-api")
token = service.issue("acme")
# curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/tasks → 200
```

```bash
pytest examples/task_api/tests/test_jwt_auth.py -q   # 成功/缺失/篡改/过期/公开路径 5 项测试
```

**为什么能无缝替换**：http 的 gate 只认 `ctx.get("auth")` 的接口（而不是 import 某个类）；worker 侧（tasks/quota/audit）不关心认证实现——这就是“服务契约 + 声明式装配”的扩展力：**改配置即可换实现，插件间只认接口不认类**。

**同样的模式可以继续扩展**：把 `TaskStore` 换 SQLite/PG（存储插件）、把 `audit` 换 OpenTelemetry（观测插件）、新增“签名校验”瀑布插件（限流同款）——都是“写一个插件 + 配置加一行”。

---

## 8. 核心 API 速查

| 想做什么           | 用什么                                                       | 一句话说明                          |
| ------------------ | ------------------------------------------------------------ | ----------------------------------- |
| 能力可插拔、可拆除 | `ctx.provide()` / `ctx.effect()` / `Fiber.dispose()`         | 可逆副作用是全部语义的底座          |
| 声明式装配应用     | `Loader.include("app.yml")` → `reconcile / disable / enable` | 配置文件即架构图（JSON/YAML/TOML）  |
| 依赖升级护栏       | `Service.version` + `@require("svc", ">=1.0")`               | 版本/接口契约，不满足=软等待        |
| 中间件 / 拦截链    | `ctx.on(event, handler)` + `waterfall(..., fallback=...)`    | 不调用 `next()` 即拦截（限流/鉴权） |
| 多租户             | `ctx.isolate(name, realm)` + `internal/config` overlay       | realm 服务隔离 + 租户配置派生       |
| 不可信插件边界     | `Context.filtered()` / `create_scope` / `scope_target`       | 事件协调式可见性隔离（非安全沙箱）  |
| 跨进程服务         | `Bridge.serve/connect` + `expose/proxy`                      | 远程服务代理 + 事件贯通             |
| 开发期热替换       | `HMR(loader)` + `watch([...])`                               | 依赖图分类 + 事务式重载，失败回滚   |
| 插件自动发现       | `discover()` / `load_entry_points()`                         | Python 包入口点（插件市场形态）     |

## 9. 质量与边界

- **验证**：111 个核心单元/集成测试 + 案例 11 个测试（含 JWT 契约、watcher 原子保存、配置热更、Bridge 协议、HMR 依赖图）；ruff 全绿；`py.typed` 随包发布。
- **可选依赖**：PyYAML / watch / pydantic 均为 extra，核心零第三方运行时依赖（仅 `packaging` 用于版本约束）。
- **诚实边界**：作用域隔离是协调式（事件/服务可见性），**不是**恶意代码的安全边界（OS 级沙箱属宿主职责）；Bridge 无认证/加密层，限受信内网；跨进程调用仅 JSON 兼容值；HMR 仅纯 Python 源码模块。
- **版本**：`0.9.2`；0.x 阶段 API 按语义化演进，1.0 前事项：对外契约类型化（mypy）、CI、并发不变量压测。

## 参考

- 论文：_A Programming Paradigm for Spatiotemporal Composability_
- Node.js 版：Cordis v4 / DeepSeek Harness vendor（本实现完全以其语义为基准，不参考其他 Python Cordis 实现）
