# 配置 overlay / 租户派生 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers 的 executing-plans 按任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 增加 internal/config waterfall 机制（激活前改写插件配置，owner 作用域过滤）与 deep_merge 工具。

**Architecture:** Context 抽出瀑布链 helper 并实现 `_resolve_config_overlay`；Fiber._invoke_plugin 先 overlay 后校验；utils 增加 deep_merge。对齐 Node vendor `fiber._resolveConfig`。

参考：`docs/specs/2026-08-28-config-overlay-design.md`

---

### Task 1: deep_merge 工具

- [ ] utils.py 增加 `deep_merge`（映射递归、其他整体替换、None 跳过）
- [ ] `__init__.py` 导出 deep_merge
- [ ] 测试：嵌套合并、列表替换、标量替换、None 跳过

### Task 2: 瀑布链 helper 与 internal/config 分发

- [ ] `context.py` 抽出 `_run_waterfall(listeners, args)`（公共 waterfall 复用）
- [ ] `_config_listeners_for(fiber)`：按 owner 严格祖先过滤（root 监听器全量生效）
- [ ] `_resolve_config_overlay(fiber, config)`：无监听器直返，否则跑链
- [ ] 测试：root 改写后代；子 ctx 只影响后代（兄弟/自身不触发）；多监听器链式；短路

### Task 3: Fiber 接入 overlay

- [ ] `fiber.py` `_invoke_plugin`：先 `await ctx._resolve_config_overlay(...)` 再 `resolve_plugin_config`
- [ ] 测试：update() 后重新 overlay；overlay 结果非法 → ConfigValidationError；async handler（await next）；sync 路径

### Task 4: 文档与提交

- [ ] README 特性 + 示例；HTML TODO 更新；DEVELOPMENT.md 更新
- [ ] pytest + ruff 全绿；中文 commit
