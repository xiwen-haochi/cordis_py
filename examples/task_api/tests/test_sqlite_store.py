"""SQLite 持久化插件测试：契约一致 + 跨实例数据保留 + 租户隔离。"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from main import make_app


def _headers(tenant: str = "acme", key: str = "key-acme") -> dict[str, str]:
    return {"X-Tenant": tenant, "X-API-Key": key}


def test_sqlite_persistence_across_app_instances(tmp_path, monkeypatch) -> None:
    """两次独立应用实例（模拟重启）共享同一 SQLite 库：数据保留。

    conftest 已注入 CORDIS_SQLITE_PATH（临时库），此测试验证
    “重启后任务仍在” —— 这正是 SQLite 后端相对内存 TaskStore 的价值。
    """
    db_path = os.environ["CORDIS_SQLITE_PATH"]

    with TestClient(make_app()) as client:
        created = client.post(
            "/api/tasks", json={"title": "持久化任务", "priority": 2}, headers=_headers()
        )
        assert created.status_code == 201
        assert created.json()["id"].startswith("acme-")

    # 第二次应用实例（等价重启）：数据从 SQLite 读回。
    with TestClient(make_app()) as client:
        listed = client.get("/api/tasks", headers=_headers()).json()
        assert listed["count"] == 1
        assert listed["items"][0]["title"] == "持久化任务"

    assert os.path.exists(db_path)


def test_sqlite_tenant_isolation(tmp_path, monkeypatch) -> None:
    """租户隔离在 SQLite 后端下依然成立（realm 键空间物理隔离）。"""
    with TestClient(make_app()) as client:
        client.post(
            "/api/tasks", json={"title": "acme 私有"}, headers=_headers("acme")
        )
        acme = client.get("/api/tasks", headers=_headers("acme")).json()
        globex = client.get("/api/tasks", headers=_headers("globex", "key-globex")).json()
        assert acme["count"] == 1
        assert globex["count"] == 0


def test_sqlite_id_sequence_recovery(tmp_path, monkeypatch) -> None:
    """id 自增序号从库中恢复：重启后新任务不与旧任务冲突。"""
    with TestClient(make_app()) as client:
        first = client.post(
            "/api/tasks", json={"title": "第一条"}, headers=_headers()
        ).json()
        assert first["id"] == "acme-1"

    with TestClient(make_app()) as client:
        second = client.post(
            "/api/tasks", json={"title": "第二条"}, headers=_headers()
        ).json()
        assert second["id"] == "acme-2"  # 序号从库中 MAX(seq)+1 恢复


def test_sqlite_delete_and_get(tmp_path, monkeypatch) -> None:
    """delete / get / 404 语义与内存 TaskStore 一致。"""
    with TestClient(make_app()) as client:
        created = client.post(
            "/api/tasks", json={"title": "待删除"}, headers=_headers()
        ).json()
        task_id = created["id"]

        assert client.get(f"/api/tasks/{task_id}", headers=_headers()).status_code == 200
        assert client.get("/api/tasks/nope", headers=_headers()).status_code == 404

        deleted = client.delete(f"/api/tasks/{task_id}", headers=_headers())
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] == task_id
        assert client.get(f"/api/tasks/{task_id}", headers=_headers()).status_code == 404
