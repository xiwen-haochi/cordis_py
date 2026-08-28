# 服务版本约束、接口校验与配置校验 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers 的 executing-plans 或 subagent-driven-development 按任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 为 cordis_py 增加服务版本约束、接口校验与配置校验，约束不满足时保持 PENDING（软等待，对齐 Node `Service.check`）。

**Architecture:** 提供方在 `provide`/`Service` 上声明版本；消费方用 `@require` 声明约束（PEP 440 specifier 或服务对象谓词）；`Fiber._compute_target` 的依赖评估加入约束检查并维护 `unsatisfied` 诊断；插件 `Config` 属性在激活前校验/转换配置。新增错误 `InvalidRequirement` 与 `ConfigValidationError`。

**Tech Stack:** Python 3.12、pytest/pytest-asyncio、ruff、`packaging>=24`（核心依赖）、pydantic（可选 extra）。

参考设计文档：`docs/specs/2026-08-28-service-contract-design.md`

---

### Task 1: 依赖、错误类型与契约辅助函数

**Files:**
- Modify: `pyproject.toml`（dependencies + pydantic extra）
- Modify: `src/cordis_py/errors.py`（新增两个错误）
- Modify: `src/cordis_py/utils.py`（新增 normalize_constraint / constraint_matches / resolve_requirements / resolve_plugin_config）
- Test: `tests/test_contract.py`

- [ ] **Step 1: pyproject.toml 增加依赖**

```toml
dependencies = ["packaging>=24"]
# [project.optional-dependencies]
pydantic = ["pydantic>=2"]
```

- [ ] **Step 2: 安装依赖并验证**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv sync --extra dev`
（若 uv cache 不可用，则 `pip install packaging` 进 .venv）

- [ ] **Step 3: errors.py 新增错误类型**

```python
class InvalidRequirement(CordisError):
    """当插件声明的要求无效（未在 inject 中声明、specifier 语法错误等）时抛出。"""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(
            "INVALID_REQUIREMENT",
            f"invalid requirement for {name!r}: {reason}",
        )


class ConfigValidationError(CordisError):
    """当插件配置未通过 Config 校验器时抛出。"""

    def __init__(self, plugin: str, reason: str) -> None:
        super().__init__(
            "CONFIG_VALIDATION",
            f"invalid config for plugin {plugin!r}: {reason}",
        )
```

- [ ] **Step 4: utils.py 增加辅助函数**（代码见下）

```python
def resolve_requirements(plugin, inject):
    """读取并规范化插件的 requirements 声明（name -> 约束列表）。"""
    raw = getattr(plugin, "requirements", None) or {}
    if not isinstance(raw, Mapping):
        raise TypeError(f"invalid requirements declaration: {raw!r}")
    out = {}
    for name, constraints in raw.items():
        if name not in inject:
            raise InvalidRequirement(name, f"service is not declared in inject")
        if isinstance(constraints, (str, Callable)):
            constraints = [constraints]
        if not isinstance(constraints, Iterable) or isinstance(constraints, (str, bytes)):
            raise InvalidRequirement(name, "constraints must be a string, callable, or a list of them")
        out[name] = [normalize_constraint(name, c) for c in constraints]
    return out


def normalize_constraint(name, constraint):
    """规范化单个约束：str 解析为 SpecifierSet，callable 原样保留。"""
    if isinstance(constraint, str):
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        try:
            return SpecifierSet(constraint)
        except InvalidSpecifier as exc:
            raise InvalidRequirement(name, f"invalid specifier {constraint!r}: {exc}") from exc
    if callable(constraint):
        return constraint
    raise InvalidRequirement(name, f"unsupported constraint type: {type(constraint).__name__}")


def constraint_matches(constraint, value, version):
    """返回 (是否满足, 原因)；满足时原因为 None。

    版本约束：提供方未声明版本或版本号非法视为不满足（保守语义）。
    接口谓词：谓词异常视为不满足并记录异常消息。
    """
    from packaging.version import InvalidVersion
    if isinstance(constraint, SpecifierSet):
        if version is None:
            return False, "服务未声明版本"
        try:
            ok = constraint.contains(version)
        except InvalidVersion:
            return False, f"服务版本 {version!r} 非法"
        if ok:
            return True, None
        return False, f"版本 {version!r} 不满足约束 {constraint}"
    try:
        ok = bool(constraint(value))
    except Exception as exc:
        return False, f"接口谓词异常: {exc}"
    return ok, None if ok else "接口谓词不满足"


def resolve_plugin_config(plugin, config):
    """按插件 Config 属性校验并转换配置（返回转换后的配置）。"""
    schema = getattr(plugin, "Config", None)
    if schema is None:
        return config
    name = getattr(plugin, "name", None) or getattr(plugin, "__name__", type(plugin).__name__)
    if inspect.isclass(schema) and hasattr(schema, "model_validate"):
        try:
            return schema.model_validate(config)
        except Exception as exc:
            raise ConfigValidationError(name, str(exc)) from exc
    if callable(schema):
        try:
            result = schema(config)
        except Exception as exc:
            raise ConfigValidationError(name, str(exc)) from exc
        return config if result is None else result
    raise TypeError(f"invalid plugin config schema for {name!r}")
```

- [ ] **Step 5: 编写并通过 utils 单测**（tests/test_contract.py，代码见 Task 6 汇总表述：normalize/constraint_matches/resolve_requirements/resolve_plugin_config 各自断言）

- [ ] **Step 6: 运行测试与 ruff**

Run: `.venv/bin/pytest tests/test_contract.py -q`、`.venv/bin/ruff check src tests`

---

### Task 2: 提供方版本（provide/Service）

**Files:**
- Modify: `src/cordis_py/context.py`（ServiceEntry.version、provide version=?）
- Modify: `src/cordis_py/service.py`（Service.version + __init__(version=)）
- Test: `tests/test_contract.py`

- [ ] **Step 1: 修改 ServiceEntry 与 provide**

```python
@dataclass
class ServiceEntry:
    name: str
    value: Any
    provider: Fiber
    version: str | None = None
```

```python
def provide(self, name: str, value: Any, *, version: str | None = None) -> Disposable:
    ...
    self._root._services[key] = ServiceEntry(name, value, fiber, version)
```

- [ ] **Step 2: Service 支持类属性与构造参数版本**

```python
class Service:
    provide: str | None = None
    version: str | None = None

    def __init__(self, ctx, name=None, *, version=None) -> None:
        self.ctx = ctx
        self.name = name or self.provide or type(self).__name__.lower()
        self.version = version if version is not None else type(self).version
        ctx.provide(self.name, self, version=self.version)
```

- [ ] **Step 3: 测试**（类属性版本、构造参数覆盖、provide 关键字版本）

---

### Task 3: `@require` 装饰器

**Files:**
- Modify: `src/cordis_py/service.py`
- Test: `tests/test_contract.py`

- [ ] **Step 1: 实现装饰器**

```python
def require(name: str, constraint: Any):
    """声明插件对服务的版本/接口约束（可叠加；同名的多个约束为 AND）。"""

    def decorator(target: T) -> T:
        raw = getattr(target, "requirements", None)
        reqs = dict(raw) if isinstance(raw, Mapping) else {}
        reqs.setdefault(name, []).append(constraint)
        target.requirements = reqs  # type: ignore[attr-defined]
        return target

    return decorator
```

（注意：字符串约束不能直接 append 后被 normalize 成列表——`resolve_requirements` 已将单值包装为列表，装饰器直接 append 原始值即可。）

- [ ] **Step 2: 测试**（多服务叠加、同名 AND、与类属性 requirements 共存）

---

### Task 4: Fiber 依赖评估软等待 + unsatisfied 诊断

**Files:**
- Modify: `src/cordis_py/fiber.py`
- Modify: `src/cordis_py/context.py`（`_requirement_reason`）
- Test: `tests/test_contract.py`

- [ ] **Step 1: Context 增加 `_requirement_reason`**

```python
def _requirement_reason(self, name: str, constraints: list[Any]) -> str | None:
    """返回约束不满足的原因；满足或无需约束时返回 None。"""
    entry = self._root._services.get(self._service_key(name))
    if entry is None or entry.provider.state != FiberState.ACTIVE:
        return "服务未提供"
    for constraint in constraints:
        ok, reason = constraint_matches(constraint, entry.value, entry.version)
        if not ok:
            return reason
    return None
```

- [ ] **Step 2: Fiber 增加 requirements/unsatisfied 并改造评估**

```python
# __init__ 新增参数
def __init__(self, ctx, plugin, config, inject, requirements=None, *, uid=0, is_root=False):
    self.requirements = requirements or {}
    self._unsatisfied: dict[str, str] = {}

# _compute_target 改造
def _compute_target(self):
    if not self.inject:
        return ()
    reasons = {}
    for name in self.inject:
        reason = self.ctx._requirement_reason(name, self.requirements.get(name, []))
        if reason is not None:
            reasons[name] = reason
    self._unsatisfied = reasons
    if reasons:
        return None
    return tuple(sorted(self.inject))

# unsatified 公开只读属性
@property
def unsatisfied(self) -> dict[str, str]:
    return dict(self._unsatisfied)

# _reload 中 committed 收集改用约束检查
self.committed = {
    name: self.ctx._get_active_service_value(name)
    for name in self.inject
    if self.ctx._requirement_reason(name, self.requirements.get(name, [])) is None
}
```

- [ ] **Step 3: Context.plugin 传入 requirements**

```python
requirements = resolve_requirements(plugin, inject)
fiber = Fiber(self, plugin, config, inject, requirements, uid=self._next_uid())
```

- [ ] **Step 4: 测试**（匹配/不匹配 PENDING/换版本自动激活/谓词异常/无版本保守/unsatisfied 内容/sync 与 async 路径）

---

### Task 5: Config 校验接入激活

**Files:**
- Modify: `src/cordis_py/fiber.py`（`_invoke_plugin` 入口）
- Test: `tests/test_contract.py`

- [ ] **Step 1: _invoke_plugin 校验配置**

```python
async def _invoke_plugin(self) -> Any:
    config = resolve_plugin_config(self.plugin, self.config)
    plugin = self.plugin
    if plugin is None:
        return None
    ...  # 后续调用处用 config 替换 self.config
```

- [ ] **Step 2: 测试**（callable 转换、失败 → ConfigValidationError + FAILED + sync plugin() 抛出、pydantic 形态 importorskip、Loader 配置更新触发校验）

---

### Task 6: 导出与文档

**Files:**
- Modify: `src/cordis_py/__init__.py`（导出 require/InvalidRequirement/ConfigValidationError）
- Modify: `README.md`、`docs/cordis_py_intro.html`、`docs/cordis_py_domains_and_design.html`、`DEVELOPMENT.md`

- [ ] **Step 1: __init__.py 导出新 API**（import + `__all__`）
- [ ] **Step 2: README 特性列表 + 简短示例**
- [ ] **Step 3: HTML TODO：待办移除"服务版本约束和接口校验"，已完成新增对应条目**
- [ ] **Step 4: DEVELOPMENT.md 待办方向更新**

---

### Task 7: 全量验证与提交

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/pytest -q` → 期望全部通过
Run: `.venv/bin/ruff check src tests main.py` → 期望 All checks passed

- [ ] **Step 2: 中文 commit**

```bash
git add -A && git commit -m "feat: 增加服务版本约束、接口校验与配置校验
- 提供方在 provide/Service 上声明版本，消费方用 @require 声明约束
- 约束不满足时消费者保持 PENDING（软等待，对齐 Node Service.check）
- Fiber 增加 unsatisfied 诊断；插件 Config 属性支持 callable 与 pydantic
- 新增 InvalidRequirement / ConfigValidationError；核心依赖 packaging"
```
