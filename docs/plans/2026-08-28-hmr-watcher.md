# HMR 文件监听器 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers 的 executing-plans 按任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 新增 `HMRWatcher` 文件监听器与 `HMR.watch()` 入口，把文件系统变更自动接入 HMR 事务重载。

**Architecture:** `watcher.py` 拆分“事件管道”（过滤 / debounce / 错误回调，可测）与“watchdog 后端”（默认实现，可选依赖）；事件经 `call_soon_threadsafe` 桥接回 asyncio。后端注入点支持假后端测试；另加一个真实 watchdog 冒烟测试。

参考：`docs/specs/2026-08-28-hmr-watcher-design.md`

---

### Task 1: 依赖与导出

- [ ] pyproject 增加 `watch = ["watchdog>=6"]` extra；dev 加 `"watchdog>=6"`
- [ ] `watcher.py` 空壳 + `__init__.py` 导出 `HMRWatcher`；`hmr.py` 延迟 import（watchdog 未装时不影响核心）

### Task 2: HMRWatcher 事件管道（后端注入）

- [ ] `watcher.py`：`DEFAULT_IGNORED`、`HMRWatcher.__init__(hmr, *, roots, ignored, debounce, recursive, backend=None, on_error=None)`
- [ ] `start()`：无运行事件循环 → `AsyncRequiredError`；用后端工厂启动；`notify(kind, path)` 线程安全桥接 `_accept`
- [ ] `_accept`：`deleted`/目录/非 `.py`/ignored 直接忽略；入 pending；无在途任务则启动 `_drain`
- [ ] `_drain`：debounce 后按排序处理 pending → `hmr.reload_file(path)`；异常 → `on_error(path, exc)` 不中断
- [ ] `stop()`：停后端、取消在途任务；`running` 属性
- [ ] 默认后端 `_watchdog_backend`：懒 import watchdog，未装抛带提示 ImportError；`Observer` + FileSystemEventHandler（on_modified/on_created，忽略目录事件）
- [ ] `HMR.watch(roots, ...)` 便捷方法

### Task 3: 测试

- [ ] 变更 `.py` → 自动重载（服务值 v1→v2）
- [ ] ignored / 非 `.py` / `deleted` / 目录事件 → 执行计数不变
- [ ] debounce 合并：连续 3 事件 → 只重载一次
- [ ] 重载失败 → on_error 收到；后续变更仍有效
- [ ] stop 后 notify 无效
- [ ] 真实 watchdog 后端冒烟（写文件 → 事件 → 重载，超时上限）

### Task 4: 文档与提交

- [ ] README HMR 示例更新为 `hmr.watch([...])`；两份 HTML TODO（已完成 + 待办移除 watcher 项）；DEVELOPMENT.md 结构/待办/测试要求
- [ ] pytest + ruff 全绿；中文 commit（docs + feat 两个提交）
