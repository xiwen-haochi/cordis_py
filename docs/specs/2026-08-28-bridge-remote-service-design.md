# 跨进程 Bridge / 远程服务 — 设计规范

> 日期：2026-08-28
> 目标：为 cordis_py 提供跨进程桥接：远端进程的插件可以像本地插件一样被调用
> （远程服务代理），事件可以双向贯通，且全部对齐 Cordis 的生命周期语义。
> 依据：Node Cordis v4 vendor 无对应内置概念（harness 的 bridge 属外部协议应用层），
> 本设计为**在 Cordis 核心语义之上的原创扩展**：客户端聚合保持可逆（fiber 效果）、
> 服务与事件遵循现有可见性语义（realm / filter）。

---

## 1. 范围

### 1.1 MVP（本次实现）

1. **传输层**：asyncio streams + JSON-lines（TCP 或 Unix socket），纯 stdlib；
2. **Bridge 服务**：双向帧协议（hello 握手 → 请求-响应调用 / 事件 / 优雅关闭）；
3. **服务代理**：远端 `expose(ctx, name, service)` 注册服务（随 fiber 反注册）；
   本地 `proxy(name)` 获得异步调用代理（`await proxy.method(*args)`）；
4. **事件贯通**：`send_event(event, *args)` / `on_event(event, listener)` 双向；
5. **生命周期**：断连/关闭 → 所有代理调用与事件监听失效，暴露明确的终止语义。

### 1.2 明确不做（后续立项）

- 流式/进度回调、双向长连接多路复用的高级语义；
- 自动“远端插件 → 本地 Loader 条目”声明式映射（`remote:` URL 等）；
- 安全层（认证/加密）与进程外沙箱联动；
- 非 JSON 兼容类型的序列化（pickle 等——安全与版本风险明确规避）。

## 2. 帧协议（JSON-lines）

每行一个 JSON 对象；字段采用最小集并按 `type` 区分：

| type | 方向 | 字段 | 说明 |
| --- | --- | --- | --- |
| `hello` | 双向（连接后） | `protocol`（=1）、`name` | 校验协议版本；不兼容则关闭 |
| `call` | 请求 → 响应 | `id`、`name`、`method`（可选）、`args`、`kwargs` | 调用远端服务；`method` 缺省时调用对象本身 |
| `result` | 响应 → 请求方 | `id`、`value` | 成功结果 |
| `error` | 响应 → 请求方 | `id`、`name`、`message`、`stack`（可选） | 异常回传（重建为 `RemoteError`） |
| `event` | 任意方向 | `event`、`args` | 事件贯通 |
| `bye` | 任意方向 | — | 优雅关闭 |

- 每帧都是独立可序列化对象（JSON 兼容叶子：dict / list / str / int / float / bool / None）；
- 请求并发：`id` 递增编号，响应按 `id` 路由回等待者；
- 不支持的帧类型：忽略并记录（不中断连接）。

## 3. API

```python
class Bridge:
    @classmethod
    async def serve(cls, *, host="127.0.0.1", port=0) -> tuple[Bridge, str]: ...
    @classmethod
    async def connect(cls, address: str) -> Bridge: ...        # host:port 或 unix 路径
    def expose(self, ctx, name, service, *, version=None) -> Fiber   # 可逆注册
    def proxy(self, name) -> RemoteService                     # 异步调用代理
    def send_event(self, event, *args) -> None                 # 发往对端
    def on_event(self, event, listener) -> Disposable          # 接收对端事件
    async def close(self) -> None                              # 优雅关闭（bye）
    @property closed: bool
```

- `expose` 以插件形式在 *ctx* 下注册：返回 `fiber`，dispose 时自动反注册；
  服务查找沿用 `ctx.provide` 语义（无版本号则按名查找，有则提供版本标记）；
- `proxy(name)` 返回的 `RemoteService`：任意属性访问得到**一次性异步调用**
  （`await proxy.method(*args)`），`proxy.__call__` 支持方法缺省调用；
- **同步模式**：远程调用必然涉及 IO，同步调用链上使用将抛出 `AsyncRequiredError`
  （不静默阻塞）；
- 事件监听器同样绑定 fiber 生命周期（`on_event` 返回 disposer）。

## 4. 生命周期与错误语义

- **握手**：连接建立后先交换 `hello`；协议不一致或 5 秒内未收到 → 抛协议错误并关闭；
- **断开**：任一侧断开 → 所有未决调用以 `RemoteClosed`（`REMOTE_CLOSED`）失败；
  已注册的代理失效（再次调用同样失败）；事件监听保留但不再触发；
  `close()` 幂等；
- **异常**：远端 `error` 帧重建为 `RemoteError`，携带原始类型名与消息，`stack` 可选；
- **事件**：事件帧只保证送达（fire-and-forget），对端已关闭时静默丢弃
  （断开语义由调用侧负责感知）。

## 5. 序列化边界

只接受 JSON 兼容值；其余类型（函数、对象、字节）在发送前
`marshal_error`/`TypeError` 拒绝，避免半序列化状态。

## 6. 测试点

1. 传输：TCP 与 Unix socket 往返；发送后对端完整收到；
2. 握手：协议一致成功；不一致报协议错误；
3. 服务调用：远端 expose → 本地 proxy → 调用返回值/参数透传（含 kwargs）；
   对象内省方法、缺省调用对象本身；
4. 错误传播：远端抛异常 → RemoteError（类型名/消息）；`RemoteClosed`（断开后调用）；
5. 并发：多个未决调用同时返回正确 `id` 路由；乱序响应；
6. 事件：双向贯通、顺序送达；fiber dispose 移除监听；
7. 生命周期：expose 随 fiber dispose 反注册；`close()` 幂等；断开后 proxy 失效；
8. 同步模式调用 → AsyncRequiredError；
9. 非 JSON 值 → 发送前拒绝。
