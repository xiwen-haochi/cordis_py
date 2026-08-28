"""JWT 认证插件测试：契约与 auth 一致（authenticate(request)）。"""

from __future__ import annotations

from types import SimpleNamespace

from plugins.jwt_auth import JwtAuthService

from cordis_py import Context


def _request(path: str = "/api/tasks", headers: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers=headers or {},
        state=SimpleNamespace(),
    )


def _service() -> JwtAuthService:
    return JwtAuthService(
        secret="test-secret", issuer="task-api", audience="task-api"
    )


def test_jwt_authenticate_success() -> None:
    service = _service()
    token = service.issue("acme")
    request = _request(headers={"authorization": f"Bearer {token}"})
    assert service.authenticate(request) is None
    assert request.state.tenant == "acme"


def test_jwt_public_path_bypass() -> None:
    service = JwtAuthService("s", public_paths=frozenset({"/api/health"}))
    request = _request("/api/health")
    assert service.authenticate(request) is None


def test_jwt_missing_or_malformed() -> None:
    service = _service()
    assert service.authenticate(_request()).status_code == 401
    assert service.authenticate(_request(headers={"authorization": "Basic xyz"})).status_code == 401
    assert (
        service.authenticate(_request(headers={"authorization": "Bearer a.b"})).status_code
        == 401
    )


def test_jwt_bad_signature_and_expired() -> None:
    service = _service()
    token = service.issue("acme")
    tampered = token[:-2] + ("a" if token[-1] != "a" else "b") * 2
    assert service.authenticate(_request(headers={"authorization": f"Bearer {tampered}"})).status_code == 401
    short = JwtAuthService("s", "task-api", "task-api")
    expired = short.issue("acme", ttl=-10)
    request = _request(headers={"authorization": f"Bearer {expired}"})
    assert service.authenticate(request).status_code == 401


def test_jwt_plugin_provides_auth_service() -> None:
    root = Context()
    root.plugin(
        lambda ctx, config: ctx.provide("auth", JwtAuthService("x")) or None, None
    )
    assert root.services["auth"] is not None
    root.fiber.dispose_sync()
