# 配置 overlay / 租户派生（internal/config waterfall）— 设计文档

日期：2026-08-28
状态：已批准

## 1. 目标

提供框架级的插件配置改写机制：插件 config 在激活前经过 `internal/config` waterfall，
父级可对其后代插件的配置做 overlay 合并与租户派生；并配套 `deep_merge` 工具。

## 2. 语义（关键决策）

- **对齐 Node vendor**：`fiber._resolveConfig` 中 `ctx.waterfall('internal/config', ...)` 先行改写，
  再进入 `Config` schema 校验——即**先 overlay、后校验**；`update()` 重载时同样经过。
- **作用域**：`internal/config` 监听器只对**注册者的严格后代 fiber** 生效（沿 `parent_fiber` 链）；
  root fiber 上注册的监听器对全体生效。兄弟分支互不干扰。
- **监听器签名**：`handler(fiber, config, next)`；`next()` 调用下游链（返回 coroutine，
  推荐 `async def` 与 `await next()`），不调用 `next()` 即短路，返回值即该阶段结果。
- **错误语义**：监听器异常视为配置错误，沿现有错误路径使 fiber FAILED。
- **派生逻辑不内置**：框架只提供机制，租户/环境派生由监听器实现；提供 `deep_merge` 作为
  overlay 分层合并基础件。

## 3. API

```python
async def tenant_overlay(fiber, config, next):
    tenant = fiber.ctx._isolation.get("tenant")   # 目标 fiber 的上下文属性
    return deep_merge(await next(), {"tenant": tenant})

ctx.on("internal/config", tenant_overlay)
```

- `utils.deep_merge(base, override)`：递归合并（映射递归、其他整体替换、`None` 跳过），
  公开导出。
- 事件名为约定字符串 `"internal/config"`，无新符号；监听器用现有 `ctx.on` 注册。

## 4. 组件

- `context.py`：
  - 抽出链式执行 helper（供公共 `waterfall` 与 internal/config 复用）；
  - `_config_listeners_for(fiber)`：收集 internal/config 监听器并按所有者祖先过滤；
  - `_resolve_config_overlay(fiber, config)`：无监听器直接返回，否则跑链。
- `fiber.py`：`_invoke_plugin` 入口改为
  `config = await ctx._resolve_config_overlay(fiber, config)` → `resolve_plugin_config`。
- `utils.py`：`deep_merge`。

## 5. 测试

- root 监听器改写后代 config；子 ctx 监听器只对后代生效（兄弟、自身均不触发）；
- 多监听器链式顺序与短路；`update()` 后重新 overlay；
- overlay 结果经 Config 校验（非法 → `ConfigValidationError`）；
- async handler（`await next()`）；sync 路径；
- `deep_merge` 嵌套合并、列表/标量替换、None 跳过。

## 6. 文档

README 特性与示例；两份 HTML TODO 状态更新；DEVELOPMENT.md 待办方向。
