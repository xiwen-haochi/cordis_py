from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest

from cordis_py import (
    AsyncRequiredError,
    Bridge,
    Context,
    RemoteClosed,
    RemoteError,
    ServiceConflict,
)


class Calculator:
    """远端测试服务。"""

    def add(self, a: int, b: int) -> int:
        return a + b

    def config(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    def fail(self) -> None:
        raise ValueError("boom")

    def bad(self) -> Any:
        # 非 JSON 兼容的返回值：发送端应拒绝。
        return {"x": {1, 2}}


class Multiplier:
    """可调用对象（缺省调用对象本身）。"""

    def __call__(self, value: int) -> int:
        return value * 2


async def _peer_ready(*bridges: Bridge) -> None:
    """等待握手完成（对端名称就绪）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3.0
    while loop.time() < deadline:
        if all(bridge.peer is not None for bridge in bridges):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("bridge handshake timeout")


async def _pair() -> tuple[Bridge, Bridge]:
    """启动服务端并连接客户端（TCP）。"""
    server, address = await Bridge.serve(port=0)
    client = await Bridge.connect(address)
    await _peer_ready(server, client)
    return server, client


async def test_remote_call_roundtrip_tcp() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "calc", Calculator())
        proxy = client.proxy("calc")
        assert await proxy.add(1, 2) == 3
        assert await proxy.config(a=1, b="x") == {"a": 1, "b": "x"}
        # 缺省调用对象本身：可调用服务。
        server.expose(root, "mult", Multiplier())
        assert await client.proxy("mult")(21) == 42
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_unix_socket_roundtrip() -> None:
    sock = f"/tmp/cordis_bridge_{os.getpid()}.sock"
    if os.path.exists(sock):
        os.remove(sock)
    server = client = None
    root = Context()
    try:
        server, address = await Bridge.serve(unix=sock)
        client = await Bridge.connect(address)
        await _peer_ready(server, client)
        server.expose(root, "calc", Calculator())
        assert await client.proxy("calc").add(40, 2) == 42
    finally:
        if client is not None:
            await client.close()
        if server is not None:
            await server.close()
        await root.fiber.dispose()
        if os.path.exists(sock):
            os.remove(sock)


async def test_remote_error_propagation() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "calc", Calculator())
        with pytest.raises(RemoteError) as info:
            await client.proxy("calc").fail()
        assert info.value.name == "ValueError"
        assert "boom" in str(info.value)
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_non_json_value_rejected() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "calc", Calculator())
        with pytest.raises(RemoteError) as info:
            await client.proxy("calc").bad()
        assert info.value.name == "TypeError"
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_concurrent_calls_routed_by_id() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "calc", Calculator())
        proxy = client.proxy("calc")
        results = await asyncio.gather(proxy.add(1, 2), proxy.add(10, 20), proxy.add(100, 200))
        assert results == [3, 30, 300]
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_failed_call_raises_when_other_pending() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "calc", Calculator())
        proxy = client.proxy("calc")
        results = await asyncio.gather(
            proxy.add(1, 2), proxy.fail(), proxy.add(10, 20), return_exceptions=True
        )
        assert isinstance(results[1], RemoteError)
        assert results[0] == 3 and results[2] == 30
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_remote_closed_after_disconnect() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "calc", Calculator())
        proxy = client.proxy("calc")
        assert await proxy.add(1, 2) == 3
        await server.close()
        # 断连后代理调用立即失败。
        with pytest.raises(RemoteClosed):
            proxy.add(1, 2)
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_events_bidirectional() -> None:
    server, client = await _pair()
    heard: list[str] = []
    try:
        disposer = client.on_event("notice", lambda value: heard.append(value))
        server.send_event("notice", "hello")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while heard != ["hello"] and loop.time() < deadline:
            await asyncio.sleep(0.01)
        assert heard == ["hello"]
        # disposer 移除监听器。
        disposer()
        server.send_event("notice", "world")
        await asyncio.sleep(0.1)
        assert heard == ["hello"]
    finally:
        await client.close()
        await server.close()


async def test_expose_disposal_unregisters() -> None:
    server, client = await _pair()
    root = Context()
    try:
        fiber = server.expose(root, "temp", Calculator())
        assert await client.proxy("temp").add(1, 1) == 2
        await fiber.dispose()
        with pytest.raises(RemoteError, match="not exposed"):
            await client.proxy("temp").add(1, 1)
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_duplicate_expose_conflict() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "dup", Calculator())
        with pytest.raises(ServiceConflict):
            server.expose(root, "dup", Multiplier())
    finally:
        await client.close()
        await server.close()
        await root.fiber.dispose()


async def test_protocol_mismatch_closes_server() -> None:
    server, address = await Bridge.serve(port=0)
    host, port = address.rsplit(":", 1)
    _reader, writer = await asyncio.open_connection(host, int(port))
    try:
        frame = json.dumps({"type": "hello", "protocol": 99, "name": "weird"})
        writer.write(frame.encode("utf-8") + b"\n")
        await writer.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while not server.closed and loop.time() < deadline:
            await asyncio.sleep(0.01)
        assert server.closed
    finally:
        writer.close()
        await server.close()


async def test_close_is_idempotent() -> None:
    server, client = await _pair()
    root = Context()
    try:
        server.expose(root, "calc", Calculator())
        await client.close()
        await client.close()
        assert client.closed
    finally:
        await server.close()
        await root.fiber.dispose()


def test_proxy_call_without_loop() -> None:
    bridge = Bridge()
    with pytest.raises(AsyncRequiredError):
        bridge.proxy("calc").add(1, 2)
