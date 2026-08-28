# 跨进程 Bridge / 远程服务 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers 的 executing-plans 按任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 新增 `bridge.py`：JSON-lines 帧协议 + asyncio 传输（TCP/Unix socket）+ 服务代理与事件贯通，全部对接 Cordis fiber 生命周期。

**Architecture:** 传输层（asyncio streams 读写行）与 Bridge 层（hello 握手、id 路由请求、事件扇出、暴露/代理）分离；`expose` 以 ctx.plugin 注册可逆；错误帧重建 RemoteError；断开 → RemoteClosed。

参考：`docs/specs/2026-08-28-bridge-remote-service-design.md`

---

### Task 1: 帧协议与传输层

- [ ] 新增 `errors.py`：`RemoteError`（`REMOTE_ERROR`）、`RemoteClosed`（`REMOTE_CLOSED`）、`ProtocolError`（`BRIDGE_PROTOCOL`）
- [ ] `bridge.py`：`_Frame` 编解码（json.dumps/loads + 每行）、`JSONLineTransport`（streams 读写、`send`/`recv`/`close`，发送时校验 JSON 兼容）
- [ ] 测试：经纬编解码；TCP/Unix 往返；仅 JSON 兼容值（非兼容 → 拒绝）
- [ ] `_json_compatible(value)` 校验函数（递归叶子检查）

### Task 2: Bridge 核心（握手 / 调用 / 事件）

- [ ] `Bridge.serve` / `Bridge.connect`（TCP host:port 或 unix socket 路径）；连接后 hello 握手（protocol=1，5 秒超时，不一致抛 ProtocolError）
- [ ] 主循环：读帧分派——`call` → 查 `_exposed` 执行 → `result`/`error`；`event` → 扇出本端监听器；`bye` → 关闭；`result`/`error` → 按 id 路由回等待者
- [ ] 并发 `_invoke(name, method, args, kwargs)`：递增 id、asyncio.Future 表、`_invoke_task`
- [ ] 断开处理：所有未决 Future 以 `RemoteClosed` 失败；`closed` 属性
- [ ] 测试：握手成功/失败；调用返回值与 kwargs；并发乱序路由；断开 → RemoteClosed；bye 优雅关闭；幂等 close

### Task 3: expose / proxy / 事件 API

- [ ] `expose(ctx, name, service, *, version=None)`：fiber 注册（dispose 反注册），帧来了按名+method 分派（method 缺省调用对象），版本标记提供
- [ ] `proxy(name)`：`RemoteService.__getattr__`（async 调用）、`__call__`；同步链上抛 AsyncRequiredError、断开抛 RemoteClosed
- [ ] `send_event` / `on_event`（fiber 绑定 disposer）
- [ ] 测试：方法调用与缺省调用；kwargs；错误传播 RemoteError（类型/消息）；expose 随 fiber dispose 反注册；on_event 随 disposer 移除；同步模式调用 → AsyncRequiredError

### Task 4: 文档与提交

- [ ] README 特性 + 桥接示例；HTML TODO（已完成 + 待办移除 Bridge 项）；DEVELOPMENT.md 结构（bridge.py）与待办清空
- [ ] pytest + ruff 全绿；中文 commit（docs + feat 两个提交）
