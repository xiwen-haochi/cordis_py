# Cordis Python 开发流程

本文档面向后续参与本项目的开发者，描述本项目的开发规范、常用命令、提交习惯和发布流程。

---

## 1. 项目定位

`cordis_py` 是 Cordis 的 Python 实现，目标是把论文中的“时空可组合性”落地为可用的 Python 动态组合框架。

核心能力：

- 可逆副作用
- 响应式依赖
- 组件/Fiber 生命周期
- 声明式加载器
- 开发期热更新
- Python 插件发现

**实现依据：**

- 论文《A Programming Paradigm for Spatiotemporal Composability》
- Node.js 版 Cordis（Cordis v4 / DeepSeek Harness vendor）

**重要约束：**

> 本项目只参考论文和 Node.js 版 Cordis，不参考任何现有的 Python Cordis 实现包。

---

## 2. 技术栈

- Python：3.12+
- 包管理：uv
- 异步：asyncio
- 测试：pytest、pytest-asyncio
- 代码检查：ruff
- 构建：hatchling
- 发布：uv build / uv publish

---

## 3. 仓库结构

```text
cordis_py/
├── DEVELOPMENT.md                 # 本开发流程文档
├── README.md                      # 项目说明与快速开始
├── pyproject.toml                 # 包配置与构建配置
├── main.py                        # 可运行演示
├── docs/
│   ├── cordis_py_intro.html       # 介绍与实现思路
│   └── cordis_py_domains_and_design.html  # 应用领域与设计优化分析
├── src/
│   └── cordis_py/
│       ├── __init__.py
│       ├── context.py             # Context 与运行时核心
│       ├── fiber.py               # Fiber 生命周期
│       ├── events.py              # 事件系统外观
│       ├── registry.py            # 插件注册外观
│       ├── service.py             # Service 基类与 inject
│       ├── loader.py              # 声明式加载器
│       ├── hmr.py                 # 开发期热更新
│       ├── discovery.py           # Python entry points 插件发现
│       ├── errors.py              # 异常定义
│       └── utils.py               # 公共工具
├── tests/                         # 单元测试
└── .pypi-token                    # 本地 PyPI token，已忽略，禁止提交
```

---

## 4. 环境准备

```bash
# 创建/进入虚拟环境
uv sync --extra dev

# 安装可编辑包
uv pip install -e ".[dev]"

# 运行测试
uv run pytest

# 运行代码检查
uv run ruff check src tests main.py
```

---

## 5. 开发约定

### 5.1 注释语言

所有注释、docstring、文档、commit message 统一使用中文。

示例：

```python
# 注册一个由当前 fiber 拥有的服务
def provide(self, name: str, value: Any) -> Disposable:
    ...
```

### 5.2 Commit 规范

每个功能点完成后单独提交，不要攒多个功能点一起提交。

Commit 类型建议：

| 类型 | 用途 | 示例 |
| --- | --- | --- |
| `feat` | 新功能 | `feat: 增加声明式加载器` |
| `fix` | 修复问题 | `fix: 修正 once 监听器` |
| `docs` | 文档 | `docs: 更新 TODO 实现状态` |
| `chore` | 工程/维护 | `chore: 完善 gitignore` |
| `refactor` | 重构 | `refactor: 调整生命周期状态机` |
| `test` | 测试 | `test: 增加响应式依赖测试` |

Commit 示例：

```bash
git add src/cordis_py/loader.py tests/test_loader.py
git commit -m "feat: 增加声明式加载器"
```

### 5.3 功能开发步骤

1. 确认该功能是否已在 TODO 文档中登记。
2. 阅读论文或 Node Cordis 对应语义，不参考其他 Python Cordis 包。
3. 设计 API 时保持与 Node 版 Cordis 概念对齐。
4. 实现核心逻辑，并同步补充注释。
5. 编写或更新测试。
6. 运行 `pytest` 和 `ruff`。
7. 更新 TODO 文档中的“已完成”/“待办”状态。
8. 使用中文 commit message 提交。
9. 阶段性完成后按发布流程发布。

### 5.4 测试要求

- 新功能必须有对应的 pytest 测试。
- 涉及异步逻辑必须使用 `pytest-asyncio`。
- 测试要覆盖：
  - 正常路径
  - 依赖未满足/依赖消失
  - 清理顺序与资源回收
  - 事件分发
  - Loader 增删改
  - HMR 基本重载

---

## 6. TODO 管理

当前 TODO 记录在两个 HTML 文档中：

- `docs/cordis_py_intro.html`
- `docs/cordis_py_domains_and_design.html`

每次完成一个功能后，需要同步更新其中的：

- “已完成”列表
- “待办/后续功能点”列表

当前待办方向：

- 跨进程 Bridge / 远程服务
- 沙箱与不可信插件隔离
- 更完整的 HMR 依赖图分类
- 配置 overlay / 租户配置派生
- 服务版本约束和接口校验

---

## 7. 代码检查

推荐在提交前运行：

```bash
uv run ruff check src tests main.py
```

如果新增了规则或修改了代码风格，也需同步更新 ruff 配置。

---

## 8. 构建与发布

### 8.1 构建

```bash
uv build
```

构建产物位于：

```text
dist/
```

`dist/` 已被 `.gitignore` 忽略，不提交到 Git。

### 8.2 发布到 GitHub

```bash
git add .
git commit -m "chore: 发布新版本"
git push origin main
```

### 8.3 发布到 PyPI

PyPI 发布名称为：

```text
cordis-python
```

本地 token 保存于：

```text
.pypi-token
```

该文件已被 `.gitignore` 忽略，严禁提交到 GitHub。

发布命令：

```bash
uv publish --token "$(cat .pypi-token)"
```

或使用环境变量避免 token 出现在命令行：

```bash
UV_PUBLISH_TOKEN="$(cat .pypi-token)" uv publish
```

---

## 9. 发布流程总结

每次阶段性完成后的标准流程：

1. 更新代码和测试。
2. 更新 TODO 文档。
3. 运行完整测试：
   ```bash
   uv run pytest
   ```
4. 运行代码检查：
   ```bash
   uv run ruff check src tests main.py
   ```
5. 确认 `.pypi-token` 没有被 Git 跟踪。
6. 构建：
   ```bash
   uv build
   ```
7. 使用中文 commit message 提交。
8. 推送 GitHub：
   ```bash
   git push origin main
   ```
9. 发布 PyPI：
   ```bash
   UV_PUBLISH_TOKEN="$(cat .pypi-token)" uv publish
   ```
10. 验证 PyPI 页面可访问。

---

## 10. 禁止提交的内容

以下内容已忽略或禁止提交：

- `__pycache__/`
- `*.py[cod]`
- `.venv/`
- `.pytest_cache/`
- `.ruff_cache/`
- `dist/`
- `build/`
- `*.egg-info/`
- `.DS_Store`
- `paper.pdf`
- `.pypi-token`

---

## 11. 完成标准 Definition of Done

一个功能点只有同时满足以下条件才算完成：

- 功能代码已实现。
- 相关测试已编写并通过。
- 注释和 docstring 为中文。
- TODO 文档已更新。
- 代码检查无明显新增问题。
- 已使用中文 commit message 提交。
- 涉及发布的阶段已同步发布到 GitHub 和 PyPI。
