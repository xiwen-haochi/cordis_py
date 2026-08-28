# 沙箱与不可信插件隔离 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers 的 executing-plans 按任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 实现 Node Cordis v4 语义的协调式隔离：`Listener.global` + `Context.filter` + 派发 receiver + 作用域路由，支撑不可信插件隔离工作流。

**Architecture:** `context.py` 增加 Listener.global、`filtered(pred)`、`_dispatch(event, receiver)` 过滤、五种派发模式 receiver 参数；`events.py` 同步镜像；新模块 `scope.py` 提供 `create_scope` / `scope_of` / `scope_target` / `bind_scope_parent`（参考 harness packages/core/scope 的 tag 路由）。

参考：`docs/specs/2026-08-28-sandbox-isolation-design.md`

---

### Task 1: 核心 filter 语义（context.py / events.py）

- [ ] `Listener` 增加 `global: bool = False`；`on` / `once` 增加 `global=False` 参数（prepend 之外）
- [ ] `Context.__init__` 增加 `_filter`（继承 parent）；`filtered(pred)` 返回带 filter 的子上下文
- [ ] `_dispatch(event, receiver=None)`：receiver 为 Context → 用 `_filter`；任意对象 → `context_filter` 属性；None → 不过滤；过滤条件 `listener.global or filter is None or filter(listener.owner.ctx)`
- [ ] `emit/parallel/serial/bail/waterfall` 增加 `receiver=None` 关键字参数并透传 `_dispatch`
- [ ] `events.py` 的 `on/once` 增加 global、五种方法增加 receiver
- [ ] 测试：global 绕过；谓词按 owner ctx；子上下文继承；五种模式一致；载体 receiver

### Task 2: 作用域路由（scope.py）

- [ ] `scope.py`：`Scope` dataclass（ctx、dispose）、`create_scope(ctx, key, *, parent=None)`（no-op 插件 fiber + key 标签 + 父链绑定）、`scope_of(ctx)`、`bind_scope_parent(child, parent)`（防环）、`scope_target(base, key)`（保留 base filter + tag 路由）
- [ ] `__init__.py` 导出 `Scope`、`create_scope`、`scope_of`、`scope_target`、`bind_scope_parent`
- [ ] 测试：同 key 可见、祖先 key 可见、无关 key 不可见、未标记全局放行、global=True 放行、base filter 保留、dispose 回收且幂等、父链防环

### Task 3: 端到端隔离示例测试

- [ ] 不可信插件 scope 工作流：scope 内注册监听器 → 可信派发（可信 receiver）不触发 → 内部派发触发；服务 realm 隔离交叉引用（isolate）

### Task 4: 文档与提交

- [ ] README 特性 + 沙箱工作流示例；HTML TODO（已完成 + 待办移除沙箱项）；DEVELOPMENT.md 结构（scope.py）与待办
- [ ] pytest + ruff 全绿；中文 commit（docs + feat 两个提交）
