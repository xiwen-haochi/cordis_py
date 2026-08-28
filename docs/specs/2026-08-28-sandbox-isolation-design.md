# 沙箱与不可信插件隔离 — 设计规范

> 日期：2026-08-28
> 目标：按 Node Cordis v4 语义实现“协调式隔离”边界——事件可见性过滤 + 作用域路由，使不可信插件可以安全地组合进运行时。
> 依据：`vendor/cordis/src/events.ts`（`Context.filter` 与 `EventOptions.global`）、`vendor/cordis/src/context.ts`（`extend` 元数据）、harness `packages/core/scope`（构建在 Cordis filter 之上的作用域路由模型）。

---

## 1. 背景与语义边界

“沙箱”分两层，本项目第一阶段只做 **Cordis 语义层**：

- **协调式隔离（本次）**：与 Node Cordis v4 完全对齐——事件监听器的**可见性边界**
  （`Context.filter` + `global` 监听器）与**服务 realm 边界**（`ctx.isolate`，已实现）。
  这是 Cordis 论文“时空可组合性”中组件边界在事件维度上的落地。
- **OS 级资源沙箱（明确不做）**：子解释器 / subprocess / 资源配额 / 文件系统限制。
  Python 没有真沙箱，恶意插件仍能 `import os` 逃逸；这属于宿主环境职责，
  后续单独立项（文档中写明边界，不制造虚假安全感）。

## 2. 核心语义（Node Cordis v4 对齐）

### 2.1 `Listener` 全局标志

`on` / `once` 增加选项 `global_`（默认 False，Python 关键字转义为下划线尾缀）。
全局监听器在每次派发时**无条件可见**，不受任何 filter 判定
（Node `EventOptions.global`：Receive the event regardless of context filter checks）。

### 2.2 `Context.filter`（派发可见性谓词）

- 每个 Context 可携带 `filter: Callable[[Context], bool] | None`；子上下文继承父的 filter，
  可用 `ctx.filtered(pred)` 派生**带自身 filter 的子上下文**（对照 Node 用 `extend({[filter]: pred})`
  设置元数据；同 `isolate`/`intercept` 的链式派生模式）。
- 语义：**以该已过滤上下文为“派发接收者（receiver）”时**，只有满足
  `listener.global_ or filter is None or filter(listener.ctx)` 的监听器会被触发
  （Node `dispatch()`：`hook.global || !filter || filter.call(thisArg, hook.ctx)`）。
  `listener.ctx` 为**监听器注册上下文**（调用 `.on` 的上下文，即“谁注册的监听器”），
  生命周期归 `listener.owner`（fiber）管理。
- 未指定 receiver 的派发行为不变（不过滤，向后兼容）。

### 2.3 派发 receiver

全部五种派发模式增加显式 `receiver` 关键字参数：

```python
ctx.emit("app/ready", receiver=scope_ctx)      # 以 scope 为接收者：只触发其可见监听器
await ctx.parallel("x", receiver=carrier)      # 接收者可以是任意“载体”
```

- receiver 为 `Context` → 使用其 `_filter`；
- receiver 为携带 `context_filter` 属性的对象（载体）→ 使用该函数；
- receiver 为 None → 不过滤。

## 3. 作用域路由（不可信插件隔离机制）

参考 harness `packages/core/scope` 的 tag 路由模型，提供**最小可用版**（新模块 `scope.py`）：

### 3.1 概念

- **scope key**：任意可哈希对象，标识一个隔离作用域。
- **scope 上下文**：由 no-op 插件 fiber 承担所有权（`create_scope`），在它之下注册的
  监听器/服务与 scope 同生共死（dispose 时整体回收）；上下文携带 scope key 标签。
- **载体（carrier）**：`scope_target(base, key)` 返回一个保留 base filter 的透明对象；
  派发时以它为 receiver → filter 放行：
  - 未标记的监听器（无 scope key）——全局可见；
  - 监听器 owned 于**同 key 或任一祖先 key** 的作用域——事件只向上流，不向下；
  - 其余被过滤。
- **祖先链**：`bind_scope_parent(child_key, parent_key)` 声明作用域父子关系
  （如“租户 → 租户内会话”），路由时沿链回溯判定。

### 3.2 API

```python
class Scope:
    ctx: Context          # 作用域上下文（注册入口）
    async def dispose()   # 回收 scope 与全部注册

def create_scope(ctx, key, *, parent: Any | None = None) -> Scope
def scope_of(ctx) -> Any | None                      # 读取上下文最近的 scope key
def scope_target(base, key) -> Any                   # 生成带路由 filter 的载体
def bind_scope_parent(child_key, parent_key) -> None
```

### 3.3 不可信插件工作流（文档示例）

```python
scope = create_scope(root, key="market/plugin-x")
# 插件全部注册在 scope.ctx 下：监听器、服务、内部事件派发
root.emit("plugin-x/ready", receiver=scope_target(root, "market/plugin-x"))
# 外部派发时以可信接收者为 receiver：不可信插件的监听器不会被触发
# 插件内部事件也只见自身（事件不向下流）
await scope.dispose()
```

## 4. 边界

- filter/scope 是**协调式**隔离：约束的是 Cordis 语义（事件、服务可见性），
  不是恶意代码的真实安全边界；禁止用其处理**不可信二进制/任意代码执行**。
- 服务侧同样的“协调式”边界由已实现的 `isolate`/realm 承担（本功能与其正交）。
- `internal/*` 内部事件不属于应用派发路径，不参与 receiver 过滤
  （与 Node 内部监听器标记 `global: true` 的语义保持一致的效果）。

## 5. 测试点

1. `global_` 监听器在 filter 生效时仍被调用；非 global_ 同场景被过滤。
2. `filtered(pred)`：谓词按监听器注册上下文判定；子上下文继承父 filter。
3. 五种派发模式（emit/parallel/serial/bail/waterfall）在 receiver 下行为一致。
4. receiver 为 Context 或载体（`context_filter` 属性）均可。
5. scope：同 key 可见；祖先 key 可见（向上流，不向下）；无关 key 不可见；
   未标记监听器全局放行；`global_=True` 放行；base filter 保留。
6. `create_scope`/`dispose`：scope 注册随 dispose 回收；重复 dispose 幂等。
7. 端到端：不可信插件在 scope 下注册监听器，外部可信派发（receiver=可信载体）
   不会触发，scope 内派发可触发。
