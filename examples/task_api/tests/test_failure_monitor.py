"""失败监控插件测试：4xx/5xx 写入 err.log，正常请求不写。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import make_app


def _headers(tenant: str = "acme", key: str = "key-acme") -> dict[str, str]:
    return {"X-Tenant": tenant, "X-API-Key": key}


def _read_log(path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_404_writes_to_err_log(tmp_path, monkeypatch) -> None:
    """不存在的任务 id → 404 记录；记录含路径与状态码。"""
    err = tmp_path / "err.log"
    monkeypatch.setenv("CORDIS_FAIL_LOG", str(err))
    with TestClient(make_app()) as client:
        resp = client.get("/api/tasks/nope", headers=_headers())
        assert resp.status_code == 404
    lines = _read_log(err)
    assert len(lines) == 1
    assert "/api/tasks/nope" in lines[0]
    assert "404" in lines[0]


def test_401_not_captured_by_waterfall_monitor(tmp_path, monkeypatch) -> None:
    """边界测试：401 在 gate 认证层被拦截，不经过 http/request 瀑布链。

    因此失败监控插件（挂在瀑布链上）记录不到 401 —— 这是架构边界：
    监控的是"瀑布链内的失败"，认证失败属于 gate 前置层。
    """
    err = tmp_path / "err.log"
    monkeypatch.setenv("CORDIS_FAIL_LOG", str(err))
    with TestClient(make_app()) as client:
        resp = client.get("/api/tasks")
        assert resp.status_code == 401
    # 401 不进瀑布链 → err.log 不产生该记录（也可能为空）
    lines = _read_log(err)
    assert all("401" not in line for line in lines)


def test_success_not_logged(tmp_path, monkeypatch) -> None:
    """正常请求（200）不写 err.log。"""
    err = tmp_path / "err.log"
    monkeypatch.setenv("CORDIS_FAIL_LOG", str(err))
    with TestClient(make_app()) as client:
        resp = client.get("/api/health", headers=_headers())
        assert resp.status_code == 200
    assert not err.exists()


def test_422_writes_with_tenant(tmp_path, monkeypatch) -> None:
    """校验失败（422）记录且含租户名。"""
    err = tmp_path / "err.log"
    monkeypatch.setenv("CORDIS_FAIL_LOG", str(err))
    with TestClient(make_app()) as client:
        resp = client.post("/api/tasks", json={"title": ""}, headers=_headers())
        assert resp.status_code == 422
    lines = _read_log(err)
    assert len(lines) == 1
    assert "422" in lines[0]
    assert "acme" in lines[0]
