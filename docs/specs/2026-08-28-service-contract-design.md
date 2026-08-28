# 服务版本约束、接口校验与配置校验 — 设计文档

日期：2026-08-28
状态：已批准（2026-08-28）

## 1. 目标

为 cordis_py 增加三项契约能力，统一在一个机制下：**约束谓词 + 软等待**。

- **服务版本约束**：提供方声明版本，消费方用 PEP 440 specifier 声明约束。
- **接口校验**：消费方用 callable 谓词校验服务对象（`hasattr`、`isinstance(Protocol)` 等）。
- **配置校验**：插件通过 `Config` 属性声明校验器/转换器。

## 2. 语义（关键决策）

- **软等待**：约束不满足时消费者保持 `PENDING`（与 Node 版 Cordis 的 `Service.check` 谓词语义一致）；提供方变化后自动重新评估。约束失败不是错误。
- **保守语义**：提供方未声明版本 + 消费方有版本约束 → 视为不匹配（等待），等提供方声明版本后自动激活。
- **声明期错误**：`require` 声明本身非法（服务名未在 inject 中、specifier 语法错误）→ 注册时抛 `InvalidRequirement`。
- **谓词异常**：接口谓词抛异常视为"不满足"，原因中记录异常消息（不打断加载链）。

## 3. API

### 3.1 提供方声明版本

```python
class Model(Service):
    version = "1.0.0"

    def __init__(self, ctx):
        super().__init__(ctx, "model")
```

构造参数形式：`super().__init__(ctx, "model", version="1.0.0")`（显式参数优先于类属性）。
自由插件：`ctx.provide("model", value, version="1.0.0")`。

### 3.2 消费方约束

```python
@inject("model")
@require("model", ">=1.0")
@require("db", lambda svc: hasattr(svc, "query"))
def plugin(ctx, config): ...
```

- `@require(name, constraint)` 可多次叠加；同一 name 的多个约束为 AND 关系。
- `constraint` 形态：
  - `str`：PEP 440 specifier（`packaging.specifiers.SpecifierSet` 解析，解析失败 → `InvalidRequirement`）；
  - `callable`：接收服务对象，返回真值 = 满足。
- 类插件可用类属性 `requirements = {"model": [">=1.0"]}`（与装饰器并存，装饰器会合并）。

### 3.3 配置校验

```python
# 形态 1：callable（校验 + 可选转换；返回 None = 原样通过）
def validate(config):
    assert config.get("name"), "name is required"
    return {"name": config["name"], "timeout": 5}

plugin.Config = validate

# 形态 2：pydantic 模型（可选 extra）
class PluginConf(BaseModel):
    name: str

plugin.Config = PluginConf
```

- 校验时机：fiber 激活前（`_reload` 入口）与 `update()` 后的重新加载；失败 → `ConfigValidationError`，fiber 进入 `FAILED`（同步路径 `plugin()` 立即抛出）。
- 普通函数作为 callable 时会直接调用；若 `Config` 是 pydantic 模型类（检测 `model_validate`），走模型校验。
- 其他形态（如实例、非 callable）→ `TypeError("invalid plugin config schema ...")`。

## 4. 组件与数据流

### 4.1 数据

- `ServiceEntry` 增加 `version: str | None` 字段（`provide` 增加 `version=` 关键字参数）。
- `Fiber` 增加：
  - `requirements: dict[str, list[Any]]`（已规范化的约束；`specifier` 已解析为 `SpecifierSet`）；
  - `unsatisfied: dict[str, str]`（最近一次 `refresh()` 评估出的不满足原因；满足则无该键）。
- `utils.py` 新增：
  - `normalize_constraint(name, constraint)`：str → `SpecifierSet`（失败抛 `InvalidRequirement`）；callable → 原样；其他 → `InvalidRequirement`；
  - `constraint_matches(constraint, value, version) -> tuple[bool, str | None]`：无版本的版本约束、非法版本号、谓词异常均返回"不满足 + 原因"；
  - `resolve_requirements(plugin, inject)`：读取/合并 `requirements` 声明，校验 name ⊆ inject，逐个规范化；
  - `resolve_plugin_config(plugin, config)`：按上述两种形态校验/转换，失败包装为 `ConfigValidationError`。
- `service.py` 新增 `require` 装饰器（`target.requirements` dict 合并）。

### 4.2 依赖评估（软等待）

`Fiber._compute_target` 与 `_reload` 的 committed 收集改为：

- 服务不存在 → 不满足（原因："服务未提供"）；
- 版本约束与接口谓词全部通过 → 满足；
- 任一约束不满足 → `PENDING`，同时填充 `fiber.unsatisfied`（提供方变化 → `_notify` → `refresh()` → 重评，与现有响应式一致）。

### 4.3 配置校验时机

`Fiber._invoke_plugin` 入口调用 `resolve_plugin_config`，返回转换后的 config 再传给插件；校验异常沿现有错误路径处理（`_error` + `FAILED`；同步 `plugin()` 抛出）。

## 5. 错误类型（新增）

| 类型 | code | 场景 |
| --- | --- | --- |
| `InvalidRequirement` | `INVALID_REQUIREMENT` | require 未在 inject 中声明、specifier 语法错误、不支持的约束形态 |
| `ConfigValidationError` | `CONFIG_VALIDATION` | Config 校验/转换失败（带插件名与原异常） |

两类型导出到 `__init__.py`；`require` 装饰器一并导出。

## 6. 依赖

- 核心依赖新增 `packaging>=24`（PEP 440 解析，pip/uv 生态事实标准）。
- 新增可选 extra：`pydantic = ["pydantic>=2"]`（不安装时仍可用 callable 形态）。

## 7. 测试覆盖

- 版本匹配 / 不匹配（PENDING + `unsatisfied` 原因）/ 提供方更换版本后自动激活；
- 接口谓词满足 / 不满足 / 抛异常；
- 提供方无版本 + 消费方有约束 → 等待；
- `require` 未声明服务 / 非法 specifier → `InvalidRequirement`；
- `Service.version` 类属性与构造参数两种提供方式；
- `Config` callable 转换与失败路径 → `ConfigValidationError`；
- pydantic 形态（`pytest.importorskip`）；
- sync 与 async 两条路径；
- Loader 配置更新触发重新校验。

## 8. 文档

- README：特性列表新增条目 + 简短示例；
- `docs/cordis_py_intro.html` / `docs/cordis_py_domains_and_design.html`：TODO 状态更新（待办 → 已完成）；
- `DEVELOPMENT.md`：待办方向移除已完成项。
