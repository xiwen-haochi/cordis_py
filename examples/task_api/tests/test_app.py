"""案例集成测试：TestClient 全链路（每测试独立应用实例）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import make_app


@pytest.fixture
def client() -> TestClient:
    with TestClient(make_app()) as test_client:
        yield test_client


def _headers(tenant: str = "acme", key: str = "key-acme") -> dict[str, str]:
    return {"X-Tenant": tenant, "X-API-Key": key}


def test_health(client: TestClient) -> None:
    response = client.get("/api/health", headers=_headers())
    assert response.status_code == 200
    assert response.json()["tenants"] == ["acme", "globex"]


def test_unauthorized(client: TestClient) -> None:
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"X-Tenant": "acme"}).status_code == 401


def test_task_crud_and_tenant_isolation(client: TestClient) -> None:
    created = client.post(
        "/api/tasks", json={"title": "写文档", "priority": 2}, headers=_headers()
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert task_id.startswith("acme-")

    listed = client.get("/api/tasks", headers=_headers()).json()
    assert listed["count"] == 1

    # 租户隔离：globex 看不到 acme 的任务。
    other = client.get("/api/tasks", headers=_headers("globex", "key-globex")).json()
    assert other["count"] == 0

    assert client.get("/api/tasks/nope", headers=_headers()).status_code == 404
    assert client.delete(f"/api/tasks/{task_id}", headers=_headers()).status_code == 200
    assert client.get("/api/tasks", headers=_headers()).json()["count"] == 0


def test_rate_limit(client: TestClient) -> None:
    for _ in range(5):
        assert client.get("/api/tasks", headers=_headers()).status_code == 200
    assert client.get("/api/tasks", headers=_headers()).status_code == 429


def test_metrics(client: TestClient) -> None:
    client.post("/api/tasks", json={"title": "x"}, headers=_headers())
    client.get("/api/tasks", headers=_headers())
    snapshot = client.get("/api/metrics", headers=_headers()).json()
    assert snapshot["taskapi.tasks.created"] == 1
    assert snapshot["taskapi.tasks.list"] == 1


def test_reactive_dependency_registration(client: TestClient) -> None:
    """tasks 在 http / tenant 之前装配：依赖出现后自动激活并注册全部路由。"""
    assert client.get("/api/health", headers=_headers()).status_code == 200
    for path in ("/api/tasks", "/api/metrics"):
        assert client.get(path, headers=_headers()).status_code in (200, 401)
