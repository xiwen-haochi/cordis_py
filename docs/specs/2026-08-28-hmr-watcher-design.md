# HMR 文件监听器 — 设计规范

> 日期：2026-08-28
> 目标：把文件系统变更自动接入 HMR 重载，提供与 Node Cordis v4 `vendor/hmr` watcher 对齐的集成能力。
> 依据：`vendor/hmr/src/index.ts`（watcher 初始化与 onChange 事件语义）。

---

## 1. 背景与问题

`HMR` 已经能按文件/模块做事务式重载，但触发方式需要手动调用 `reload_file` / `reload_module`。
开发期真正需要的是：保存源码后**自动**发现变更并重载受影响条目。

Node Cordis v4 的 HMR 在插件激活时就对 `root` 目录启动 chokidar 监听，并在事件回调里完成
“config 刷新 / externals 全量重启 / 模块部分重载”的分流。Python 版移植其中与本项目相关的部分：

- 源码文件（`.py`）变更 → 触发 HMR 事务重载；
- 事件合并（debounce 100ms）；
- 忽略常见垃圾目录（对齐 Node 的 `.**`/node_modules，外加 Python 的 `__pycache__`/`.venv`）；
- 重载失败不打断观察（HMR 自身已回滚，错误走回调/日志）。

## 2. 关键设计

### 2.1 观察后端与事件管道分离

- **管道（核心，可测）**：`HMRWatcher` 负责事件过滤、debounce 合并、触发 `hmr.reload_file`、
  错误回调、停止清理。与具体观察后端解耦。
- **后端（薄适配层）**：基于 watchdog（可选依赖，`cordis-python[watch]`）。watchdog 在独立
  线程回调事件，经 `loop.call_soon_threadsafe` 桥接回 asyncio 事件循环。
- 后端接口只要求 `start()` / `stop()`；测试注入假后端，冒烟测试用真实 watchdog。

### 2.2 事件语义（对齐 Node onChange 的分流）

| 事件 | 处理 |
| --- | --- |
| `changed` / `created`（`.py` 文件，未忽略） | 加入 pending 集合，debounce 后 `hmr.reload_file` |
| `deleted` | 忽略（已加载模块的源码删除不热更） |
| 目录事件 | 忽略 |
| 非 `.py` 文件 | 忽略（等价于 Node 用 ESM loadCache 判定；reload_file 自身对非模块文件也是 no-op，双保险） |
| ignored glob 命中 | 忽略 |

- `ignored` 默认：`("**/.*", "**/__pycache__", "**/node_modules", "**/.venv", "cache", "data")`。
- `debounce` 默认 0.1 秒（trailing 合并窗口：窗口内多个事件只触发一次重载）。
- 原子保存（写临时文件再 rename）会产生 `created`，因此 `created` 与 `changed` 同等对待。

### 2.3 错误处理

- 重载失败：调用 `on_error(path, exc)` 回调（默认打印到 stderr），watcher 继续观察。
  HMR 的事务回滚保证失败后服务仍处于旧代码状态。
- watchdog 未安装：`start()`/`watch()` 时抛出带安装提示的 `ImportError`。

### 2.4 生命周期

- `HMR.watch(roots, ...)` 同步启动并返回 `HMRWatcher`（需要运行中的事件循环，否则
  `AsyncRequiredError`）；
- `watcher.stop()`：停止后端、取消在途刷新任务，事后事件不再被处理；
- `notify(kind, path)` 为线程安全的事件入口（后端回调使用）。

### 2.5 API

```python
HMR.watch(roots=(".",), *, ignored=None, debounce=0.1, recursive=True,
          backend=None, on_error=None) -> HMRWatcher

class HMRWatcher:
    def __init__(self, hmr, *, roots=(".",), ignored=DEFAULT_IGNORED,
                 debounce=0.1, recursive=True, backend=None, on_error=None) -> None
    def start(self) -> HMRWatcher
    def notify(self, kind: str, path: str) -> None   # 线程安全
    async def stop(self) -> None
    @property
    def running(self) -> bool
```

## 3. 边界与限制

- 观察器需要运行事件循环（文档写明在异步上下文中使用）。
- 同名文件在 debounce 窗口内的事件合并为一次重载（幂等，多条路径按排序逐一处理）。
- watchdog 为可选依赖；不安装时仅影响 `watch`/`start`，`HMR` 核心与手动触发不受影响。
- 实时监听延迟与文件系统事件精度取决于平台后端（macOS FSEvents / Linux inotify / 轮询）。

## 4. 测试点

1. `.py` 变更 → 自动重载（服务值更新）；
2. ignored 路径、非 `.py`、`deleted`、目录事件 → 不重载（执行计数不变）；
3. debounce 合并：连续多个事件只重载一次；
4. 重载失败 → on_error 收到 (path, exc)，后续变更仍可重载；
5. stop 后事件不再处理；
6. 真实 watchdog 后端冒烟：写文件 → 事件到达 → 重载生效。
