# Cordis Task API —— 插件化多租户 HTTP 案例

一个接近生产的开发案例：用 `cordis_py` 装配的 FastAPI 多租户任务管理 API。
展示的核心能力：声明式 Loader 装配、响应式依赖、可逆副作用、事件瀑布链（中间件）、
per-realm 租户隔离、契约校验、HMR 热替换、指标与审计。

## 架构

```text
main.py                    uvicorn 入口：宿主注入 FastAPI 应用对象，Loader 装配，可选 HMR watch
app.yml                    声明式插件装配（9 个插件，顺序刻意非依赖序）
plugins/
  logger_plugin.py         log 服务：带格式化的标准库 logger 工厂
  http_service.py          http 装配：app 中间件（认证入口 + 请求瀑布）、routes 路由注册表
  tenant.py                tenant 注册表：每租户 isolate("tasks", realm) + 专属 TaskStore
  auth.py                  auth 服务：X-Tenant / X-API-Key 校验 → request.state.tenant
  quota.py                 限流：监听 http/request 瀑布，超标短路返回 429
  audit.py                 审计：监听 task/created、task/deleted 事件写日志
  metrics.py               metrics 服务 + /api/metrics 路由
  tasks.py                 业务：任务 CRUD（响应式依赖 routes/tenants，版本契约）
  health.py                /api/health
```

要点：

- **响应式依赖**：`tasks` 插件在装配顺序中位于 `http` / `tenant` 之前，因 `routes` /
  `registry` 服务缺失而保持软等待；提供者出现后自动激活并注册路由（顺序无关）。
- **每请求中间件链**：认证在 FastAPI 中间件内校验（401 直接返回），随后
  `ctx.waterfall("http/request", tenant, request, fallback=call_next)` —— 限流等
  插件作为链条一环（不调用 `next` 即拦截），链尾是整个请求的真实处理。
- **可逆路由**：业务插件 `ctx.effect` 注册路由 disposer；HMR 重载旧插件 fiber 时
  路由自动卸载，新插件重新注册同名路由（旧 APIRoute 被替换）。
- **契约校验**：`tenant` 提供 `tenants` 服务（version=1.0），`tasks` 用
  `@require("tenants", ">=1.0")` 声明版本约束。

## 运行

```bash
cd examples/task_api
python -m venv .venv && source .venv/bin/activate  # 或使用项目环境
pip install -r requirements.txt

python main.py --port 8000             # 启动即启用 HMR（插件源码热替换，不重启；依赖见 requirements.txt）
python main.py --port 8000 --watch      # 显式开启（与默认一致）
```

## 验证

```bash
# 健康检查
curl -s http://127.0.0.1:8000/api/health

# 未认证 → 401
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/tasks

# 租户 acme 创建并列出任务
curl -s -H "X-Tenant: acme" -H "X-API-Key: key-acme" \
     -H "Content-Type: application/json" -d '{"title": "写文档", "priority": 2}' \
     http://127.0.0.1:8000/api/tasks
curl -s -H "X-Tenant: acme" -H "X-API-Key: key-acme" http://127.0.0.1:8000/api/tasks

# 租户隔离：globex 看不到 acme 的任务
curl -s -H "X-Tenant: globex" -H "X-API-Key: key-globex" http://127.0.0.1:8000/api/tasks

# 限流：同租户连续请求超过 limit 后返回 429
for i in $(seq 1 8); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "X-Tenant: acme" -H "X-API-Key: key-acme" http://127.0.0.1:8000/api/tasks
done

# 指标
curl -s http://127.0.0.1:8000/api/metrics
```

## HMR 演示（--watch 模式）

1. 修改 `plugins/quota.py` 中的 `limit` 默认值（或审计文案）；
2. 保存后观察：**不重启进程**，下一次请求立即按新代码生效；
   旧 fiber 被 dispose（服务/路由/监听器/限流状态全部卸载），新插件被重新应用；
3. 支持编辑器原子保存（临时文件 + rename，如 `sed -i` / Vim 默认行为）——
   watcher 把 moved 事件视为一次新建并触发重载；
4. 修改 `plugins/audit.py` 的日志文案，保存后在服务端日志看到新格式。

## 测试

```bash
pytest tests/ -q
```

覆盖：公开健康检查、认证 401、租户 CRUD、租户数据隔离、限流 429、指标计数、
契约校验（`tasks` 在依赖缺失时软等待后激活）。
