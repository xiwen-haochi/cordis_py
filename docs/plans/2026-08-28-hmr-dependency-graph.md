# HMR 依赖图分类 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers 的 executing-plans 按任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 将 Node Cordis v4 的 HMR 三段式（模块分类 → 过期条目检测 → 事务式重载）移植到 Python，替换最小单条目重载。

**Architecture:** 新增 `depgraph.py` 提供模块依赖图（meta_path 运行时追踪 + install 时 AST 静态补全）与 accepted/declined 分类；`hmr.py` 的 `HMR` 负责分类、受影响条目判定、拓扑序重载与失败回滚；`Loader._start_entry` 增加 `plugin=` 参数复用 isolate/intercept 链。

参考：`docs/specs/2026-08-28-hmr-dependency-graph-design.md`

---

### Task 1: Loader 支持预解析插件

- [ ] `loader.py` `_start_entry(self, entry, *, plugin=None)`：`plugin` 为 None 时 `import_string(entry.url)`，否则直接用传入对象
- [ ] 测试：现有 loader 测试全绿（行为不变）

### Task 2: ModuleGraph — 追踪器与 external 判定

- [ ] `depgraph.py` 新建：`Classification(accepted, declined)` frozen dataclass
- [ ] `ModuleGraph.__init__(exclude_prefixes=("cordis_py",))`：计算 stdlib / site-packages 路径前缀
- [ ] `is_external(name)`：stdlib 名、前缀、`__main__`、文件位于基础目录 → True
- [ ] `_ImportTraceFinder`：`find_spec` 中向上找第一个在 `sys.modules` 的栈帧为 importer；importer external 则丢弃；记录边 `importer → fullname`
- [ ] `install()`：插入 `sys.meta_path`；`uninstall()`：移除；`installed` 属性
- [ ] 测试：import / from-import / 函数体内 import 记录边；external importer 丢弃；uninstall 后停止记录

### Task 3: ModuleGraph — AST 补全与查询

- [ ] `install()` 中补全：遍历 `sys.modules` 快照，`.py` 且非 external 的模块用 `ast` 解析边（`import a.b` → `a`,`a.b`；`from pkg import y` → `pkg`，`pkg.y` 在 `sys.modules` 则加边；relative 用 `__package__`）
- [ ] `imports(name)`、`closure(name, skip=None)`（含自身、剪枝 skip/external、DFS 防环）、`file_to_module(path)`、`module_to_file(name)`
- [ ] 测试：HMR 创建前已加载模块的边可用；relative import 正确

### Task 4: 分类算法（对齐 Node analyzeChanges）

- [ ] `classify(changed)`：accepted=初始 changed；从 changed 的 imports 逐层判定（任一 import accepted → accepted；全部 declined/external → declined；未定延迟；无进展后残余 declined）
- [ ] 测试：变更 helper → accepted 含 helper、未触达兄弟 declined；无直接 import 的 changed 仅自身 accepted

### Task 5: HMR 事务式重载

- [ ] `hmr.py` 重写：`HMR(loader, *, graph=None)` 自动安装 graph；`affected(changed)` 按 2.2 插件轮求受影响条目（保持声明顺序）
- [ ] `_reload(changed)`：快照旧插件 + accepted 模块 `__dict__` → dispose 受影响 fiber → 拓扑序（imports 优先）reload accepted → reload 条目模块、`import_string` 解析新插件 → `loader._start_entry(entry, plugin=...)` 重应用；失败回滚（dispose 新 fiber → 恢复 dict 快照 → 旧插件重应用 → 重抛）
- [ ] `reload_file`（resolve 后比对 `__file__`，未匹配返回 []）、`reload_module`（未加载先 import）、`reload_entry`（条目模块视为 changed，连带重载依赖者）、`reload_all`、`dispose`（uninstall graph）
- [ ] 测试：helper 变更 → 两个共享条目重载、无关条目不重载；`from helper import x` 刷新；回滚（新代码 apply 抛错 → 旧服务仍在）；`reload_file` 未知文件 → []; `reload_entry` 连带重载

### Task 6: 导出、文档与提交

- [ ] `__init__.py` 导出 `ModuleGraph`、`Classification`
- [ ] README 更新 HMR 段；两份 HTML TODO（已完成 + 待办移除该项）；DEVELOPMENT.md 待办列表更新；HTML 4.4 草案核对
- [ ] `.venv/bin/pytest` + `.venv/bin/ruff` 全绿；中文 commit（docs 提交 + feat 提交，或合并一个 docs 提交）

---

**实现顺序：** Task 1 → 4 → 2+3（图是分类前提，先写图再写算法顺序可调，但测试先行）→ 5 → 6。
