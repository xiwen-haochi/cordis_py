# HMR 依赖图分类 — 设计规范

> 日期：2026-08-28
> 目标：将 Node.js Cordis v4 的 HMR 依赖图分类完整移植到 `cordis_py`，替换当前“一次只重载一个条目”的最小实现。
> 依据：`vendor/hmr/src/index.ts`（analyzeChanges / partialReload）、`vendor/loader/src/internal.ts`（ModuleJob 语义）。

---

## 1. 背景与问题

当前 `HMR.reload_entry()` 只重载单个 Loader 条目，无法处理：

- 修改了插件依赖的 *helper 模块*（非条目自身），插件条目不会重载；
- 多个条目共享同一依赖模块，需要整图判断谁受影响；
- `from helper import x` 形式的绑定在模块重载后依然过期；
- 重载失败时没有回滚，进程可能停留在“一半新一半旧”的状态。

Node Cordis v4 的 HMR 已经解决了这些问题：

1. **模块分类**：对变更文件集合计算 accepted / declined 集合；
2. **过期条目检测**：找出 import 依赖树触及 accepted 模块的 Entry；
3. **事务式重载**：清理缓存、dispose 旧 fiber、重新 import 并 apply，失败时恢复旧缓存与旧插件。

本设计按同样的三段式移植该算法。

---

## 2. Node 参考实现的关键语义（实证）

`ModuleJob.linked` 在 Node v24 中为“本模块的依赖 job 列表”（Promise for the list of all dependencyJobs，见 `lib/internal/modules/esm/module_job.js`），即**前向依赖边**（importer → imported）。

### 2.1 分类算法（`analyzeChanges`）

- 初始：`accepted = stashed`（直接变更的文件）；`declined = externals`（框架入口依赖树，本项目翻译为“不可重载的基础模块”）。
- 从 stashed 文件的**直接依赖**开始逐层遍历其依赖树：
  - 节点 `X` 被 accepted，当且仅当 `X` 任一直接依赖（或更深层传递依赖）已被 accepted；
  - 节点 `X` 被 declined，当且仅当 `X` 的全部直接依赖均 declined 或为 external（即 `X` 之下没有触及任何变更）；
  - 依赖状态未定者延迟到下一轮；迭代无进展后，残余 pending 一律 declined。
- 语义结论：**accepted = 变更文件本身 + 变更文件之下（依赖侧）所有“其子树内触达变更”的文件**；declined = 变更文件依赖子树中未触达变更的文件（其缓存无需失效）。变更文件的 **importer 侧**文件不会被分类遍历到，其是否重载由 2.2 的条目轮决定。

### 2.2 过期条目检测（`partialReload` 插件轮）

- 对每个插件条目解析出条目文件 URL：条目文件已被 declined → 跳过；
- 否则求条目文件的**依赖闭包**（剪枝 declined 与 external），若闭包与 accepted 有交集 → 该插件重载；
- **差异说明**：Node 在插件轮会把条目的依赖闭包并入 accepted（这是为 ESM 缓存失效服务的簿记）。Python 的 `importlib.reload` 需要显式指定才重执行模块，重载集为“变更模块 + 受影响条目模块”，未变更的中间依赖保持缓存即可——若复制 Node 的并集语义，共享依赖会把无关条目一并卷入且无谓重执行依赖的模块级副作用，故不做该并集；
- 条目文件的依赖闭包中**上方（importer 侧）的中间模块**不在分类遍历中，是否重载由“是否属于某受影响条目闭包”决定——影响传播的方向是：变更沿 import 边从被依赖文件向导入者传播，再由条目轮把受影响条目重载。

### 2.3 事务式重载要点

- 重载单位是**插件条目**：dispose 旧 fiber，重新加载条目模块并 resolve 出新的插件对象，重新 apply。
- accepted 中的文件先失效缓存；重载顺序：依赖先于导入者（拓扑序）。
- 失败时：恢复缓存快照 + 重新注册旧插件（回滚）。

---

## 3. Python 移植设计

### 3.1 模块依赖图的来源

Node 的图来自模块系统本身（loadCache / job.linked），天然完整。Python 没有等价物，采用“**追踪器 + AST 补全**”双通道：

1. **运行时追踪器**（`sys.meta_path` 前缀 finder）：
   - 在 `find_spec` 中向上找**第一个在 `sys.modules` 中的栈帧**作为 importer（CPython 依赖栈帧，项目 Python 3.12 满足）；
   - importer 为 external（stdlib / site-packages / `cordis_py` / `__main__`）→ 丢弃该边（避免框架自身导入污染图）；
   - 精确覆盖动态导入（函数体内 `import`、`importlib.import_module`）与 `from x import y`。
2. **AST 静态补全**（`ModuleGraph.install()` 时对 `sys.modules` 快照做一次）：
   - 仅处理 `__file__` 以 `.py` 结尾且非 external 的模块；
   - 解析 `import a.b`（边 → `a` 与 `a.b`）与 `from pkg import y`（边 → `pkg`，若 `pkg.y` 已在 `sys.modules` 则同时加边；relative import 依据 `__package__` 解析）；
   - 覆盖“HMR 创建前已加载”的模块（默认用法：先 `loader.reconcile()` 再 `HMR(loader)`）。

### 3.2 external 判定

`is_external(name)` 为真当且仅当满足任一：

- `name` 在 `sys.stdlib_module_names`（Python 3.10+）；
- `name` 以任一 `exclude_prefixes`（默认 `("cordis_py",)`）开头；
- `name` 为 `__main__`（主脚本变更视为全量重启，不归 HMR 管）；
- 模块文件（`__file__` / origin）位于 stdlib 或 site-packages 目录下（通过 `sysconfig` / `site` 计算一次）。

external 模块既不产生边、也不进入分类遍历、也不参与条目闭包判定。

### 3.3 分类结果

```python
@dataclass(frozen=True)
class Classification:
    accepted: frozenset[str]   # 需要重新加载（失效）的模块
    declined: frozenset[str]   # 无需重载的模块
```

输入为变更模块名集合（`changed`）；算法与 2.1 对齐，排除判定用 3.2 的 `is_external`。

### 3.4 重载事务（`HMR._reload`）

1. `classify(changed)` → 依据 2.2 求受影响条目（保持 Loader 声明顺序）；
2. 无受影响条目 → 直接返回空列表（不做事，与 Node 一致地不碰缓存）；
3. 快照：受影响条目的旧插件对象（`fiber.plugin`）+ 重载集内每个模块的 `module.__dict__` 浅拷贝；
4. dispose 全部受影响 fiber；
5. 对重载集（= 变更模块 ∪ 受影响条目模块）∩ `sys.modules` 中 `__file__` 以 `.py` 结尾的模块，按“依赖先于导入者”拓扑序 `importlib.reload`；
6. 用 `import_string(entry.url)` 解析新插件对象，按声明顺序重新 apply（复用 Loader 的 isolate/intercept 链逻辑，`_start_entry` 增加 `plugin=` 参数）；
7. 任一步失败：dispose 已 apply 的新 fiber → 恢复模块 `__dict__` 快照 → 用旧插件重新 apply → 重抛原始异常。

### 3.5 API

```python
class HMR:
    def __init__(self, loader: Loader, *, graph: ModuleGraph | None = None) -> None
    async def reload_file(self, filename: str | Path) -> list[str]   # 返回受影响 entry id 列表
    async def reload_module(self, module_name: str) -> list[str]
    async def reload_entry(self, entry_id: str) -> list[str]         # 兼容旧 API；现在连带重载依赖者
    async def reload_all(self) -> list[str]
    async def dispose(self) -> None                                  # 卸载追踪器
    def affected(self, changed: set[str]) -> list[str]               # 只分类不执行
```

- `reload_file`：`path.resolve()` 后与 `sys.modules` 中各模块 `__file__` 比对，匹配不到 → 空操作返回 `[]`（watcher 场景下非 Python / 未导入文件合法地无动作）。
- `reload_module`：模块不在 `sys.modules` 时先 `importlib.import_module`（此时其内部边经追踪器记录）。
- `reload_entry` 语义升级：把条目模块视为变更文件，分类后连其 importer 侧条目一并重载。
- `ModuleGraph` 与 `Classification` 从 `cordis_py` 顶层导出，供测试与工具读取。

### 3.6 边界与限制（写进文档）

- 仅支持纯 Python 源码模块（`.py`）；扩展模块（`.so`）与命名空间包跳过重载；
- `__main__` 或 `cordis_py` 自身变更 = 全量重启，不在 HMR 范围内；
- 安装追踪器之前已加载模块中的**动态** import 边缺失（静态部分由 AST 补全，此为已知限制）；
- 依赖图不覆盖运行时条件导入路径分支（AST 全量静态边，或许偏多：多重载优于漏重载）。

---

## 4. 测试点

1. 追踪器：`import` / `from-import` / 函数体内 import 均记录边；external importer 的边被丢弃；`uninstall()` 后不再记录。
2. AST 补全：HMR 创建前已加载的模块边可用（默认用法）。
3. 分类：变更 helper → accepted 含 helper；未触达变更的兄弟模块 declined。
4. 过期条目：两个条目共享 helper，helper 变更 → 两条目都重载；无关条目不重载。
5. `from helper import x`：helper 变更后，条目模块重载使 `x` 绑定刷新。
6. 事务回滚：新代码 apply 抛错 → 旧服务仍在、旧值可见、异常传播。
7. `reload_file`：路径 → 模块解析；未知文件 → 空操作。
8. `reload_all` / `reload_entry` 兼容性。
