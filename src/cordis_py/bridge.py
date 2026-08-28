"""跨进程桥接：JSON-lines 帧协议、服务代理与事件贯通。

在 Cordis 核心语义之上的原创扩展（Node Cordis v4 vendor 无内置对应概念）：
- 远端进程通过 :meth:`Bridge.expose` 注册“可被调用的服务”，本地
  :meth:`Bridge.proxy` 获得异步调用代理；
- 事件经 :meth:`Bridge.send_event` / :meth:`Bridge.on_event` 双向贯通；
- 全部注册对接 Cordis fiber 生命周期：expose/事件监听随 fiber dispose 回收。

安全边界：帧只接受 JSON 兼容值；跨进程调用必然是异步 IO，同步调用链上使用
代理会抛出 :class:`AsyncRequiredError`。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .context import Context
from .errors import (
    AsyncRequiredError,
    ProtocolError,
    RemoteClosed,
    RemoteError,
    ServiceConflict,
)
from .fiber import Fiber
from .utils import Disposable, has_running_loop

__all__ = ["Bridge", "RemoteService"]

PROTOCOL = 1
HELLO_TIMEOUT = 5.0

_TCP_ADDRESS = re.compile(r"^[^/]+:\d+$")


def _json_compatible(value: Any) -> bool:
    """判断值是否可安全序列化为 JSON（仅标量/列表/映射叶子）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_compatible(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_json_compatible(item) for item in value)
    return False


class JSONLineTransport:
    """asyncio streams 上的 JSON-lines 传输（每帧一行）。"""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def send(self, frame: dict[str, Any]) -> None:
        if not _json_compatible(frame):
            raise TypeError(f"frame is not JSON-serializable: {type(frame.get('value')).__name__}")
        data = json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._writer.write(data + b"\n")
        await self._writer.drain()

    async def recv(self) -> dict[str, Any] | None:
        """读取一帧；连接关闭（EOF）时返回 None。"""
        line = await self._reader.readline()
        if not line:
            return None
        try:
            frame = json.loads(line)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"malformed frame: {error.msg}") from error
        if not isinstance(frame, dict):
            raise ProtocolError(f"frame must be an object, got {type(frame).__name__}")
        return frame

    async def close(self) -> None:
        """关闭写入端；对断连/取消路径免疫（幂等）。"""
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError, asyncio.CancelledError):
            # 任务取消会让 _closed future 一并取消；断连同理。两者都视为已关闭。
            pass


@dataclass
class _ExposedService:
    """远端暴露的服务条目。"""

    name: str
    service: Any
    version: str | None


class RemoteService:
    """本地侧的远程服务代理。

    任意属性访问得到一个**一次性异步调用**：``await proxy.method(*args)``。
    不带属性直接调用（``proxy(*args)``）等价于调用暴露对象本身（method 缺省）。
    """

    def __init__(self, bridge: Bridge, name: str) -> None:
        self._bridge = bridge
        self._name = name

    def __getattr__(self, method: str) -> Callable[..., Any]:
        if method.startswith("_"):
            raise AttributeError(method)
        return self._bridge._remote_call(self._name, method)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._bridge._remote_call(self._name, None)(*args, **kwargs)


class Bridge:
    """跨进程桥：请求-响应调用、事件贯通与可逆服务暴露。"""

    def __init__(self, name: str = "bridge") -> None:
        self.name = name
        self._transport: JSONLineTransport | None = None
        self._server: asyncio.Server | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._exposed: dict[str, _ExposedService] = {}
        self._events: dict[str, list[Callable[..., Any]]] = {}
        self._peer_name: str | None = None
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._id = 0

    @property
    def closed(self) -> bool:
        """桥接是否已关闭（断连或显式关闭）。"""
        return self._closed

    @property
    def peer(self) -> str | None:
        """对端名称（hello 握手后可知）。"""
        return self._peer_name

    # ------------------------------------------------------------------
    # 建立连接
    # ------------------------------------------------------------------

    @classmethod
    async def serve(
        cls,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        unix: str | None = None,
        name: str = "server",
    ) -> tuple[Bridge, str]:
        """启动服务端并等待第一个连接；返回 ``(bridge, address)``。"""
        bridge = cls(name)
        bridge._loop = asyncio.get_running_loop()
        handler = bridge._on_connection
        if unix is not None:
            server = await asyncio.start_unix_server(handler, unix)
            address = unix
        else:
            server = await asyncio.start_server(handler, host, port)
            sock = server.sockets[0].getsockname()
            address = f"{host}:{sock[1]}"
        bridge._server = server
        return bridge, address

    @classmethod
    async def connect(cls, address: str, name: str = "client") -> Bridge:
        """连接服务端（``host:port`` 为 TCP，其余视为 Unix socket 路径）。"""
        bridge = cls(name)
        bridge._loop = asyncio.get_running_loop()
        if _TCP_ADDRESS.match(address):
            host, port = address.rsplit(":", 1)
            reader, writer = await asyncio.open_connection(host, int(port))
        else:
            reader, writer = await asyncio.open_unix_connection(address)
        bridge._adopt(reader, writer)
        return bridge

    def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """服务端连接回调（MVP：只接受第一个连接）。"""
        if self._transport is not None:
            writer.close()
            return
        self._adopt(reader, writer)

    def _adopt(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._transport = JSONLineTransport(reader, writer)
        loop = self._loop or asyncio.get_running_loop()
        self._loop = loop
        self._run_task = loop.create_task(self._run())

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        transport = self._transport
        assert transport is not None
        try:
            await transport.send(
                {"type": "hello", "protocol": PROTOCOL, "name": self.name}
            )
            first = await asyncio.wait_for(transport.recv(), HELLO_TIMEOUT)
            if first is None or first.get("type") != "hello":
                raise ProtocolError("peer did not send a hello frame")
            if first.get("protocol") != PROTOCOL:
                raise ProtocolError(
                    f"protocol mismatch: peer={first.get('protocol')} local={PROTOCOL}"
                )
            self._peer_name = str(first.get("name") or "peer")
            while True:
                frame = await transport.recv()
                if frame is None:
                    break
                if frame.get("type") == "bye":
                    break
                await self._handle(frame)
        except asyncio.CancelledError:
            raise
        except (OSError, ConnectionError, ProtocolError, TimeoutError):
            pass
        finally:
            await self._shutdown()

    async def _handle(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "call":
            await self._dispatch_call(frame)
        elif kind == "result":
            self._resolve(frame.get("id"), frame.get("value"))
        elif kind == "error":
            future = self._pop(frame.get("id"))
            if future is not None and not future.done():
                future.set_exception(
                    RemoteError(
                        str(frame.get("name") or "RemoteError"),
                        str(frame.get("message") or "remote call failed"),
                        frame.get("stack"),
                    )
                )
        elif kind == "event":
            self._dispatch_event(str(frame.get("event") or ""), list(frame.get("args") or ()))
        # 未知帧类型：忽略并继续。

    # ------------------------------------------------------------------
    # 服务暴露与代理
    # ------------------------------------------------------------------

    def expose(self, ctx: Context, name: str, service: Any, *, version: str | None = None) -> Fiber:
        """在 *ctx* 下暴露远端服务（随 fiber dispose 自动反注册）。

        注册立即生效（调用请求不会与应用启动竞态）；卸载由插件 fiber 承担，
        与 Cordis 可逆效果语义一致。
        """
        if name in self._exposed:
            raise ServiceConflict(name, self._peer_name or self.name)
        entry = _ExposedService(name, service, version)
        self._exposed[name] = entry

        def plugin(plugin_ctx: Context, config: Any) -> None:
            def undo() -> None:
                self._exposed.pop(name, None)

            plugin_ctx.effect(lambda: undo)

        return ctx.plugin(plugin, None)

    async def _dispatch_call(self, frame: dict[str, Any]) -> None:
        call_id = frame.get("id")
        name = str(frame.get("name") or "")
        method = frame.get("method")
        args = list(frame.get("args") or ())
        kwargs = dict(frame.get("kwargs") or {})
        try:
            entry = self._exposed.get(name)
            if entry is None:
                raise LookupError(f"service {name!r} is not exposed")
            target = entry.service
            if method is not None:
                target = getattr(entry.service, method, None)
                if target is None:
                    raise AttributeError(f"service {name!r} has no method {method!r}")
            result = target(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            await self._transport.send(  # type: ignore[union-attr]
                {"type": "result", "id": call_id, "value": result}
            )
        except Exception as exc:  # noqa: BLE001 - 远端异常统一回传，不中断连接
            try:
                await self._transport.send(  # type: ignore[union-attr]
                    {
                        "type": "error",
                        "id": call_id,
                        "name": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            except Exception:  # noqa: BLE001, S110 - 发送失败意味着连接已断
                pass

    def _remote_call(self, name: str, method: str | None) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            if self.closed:
                raise RemoteClosed()
            if not has_running_loop():
                raise AsyncRequiredError("远程服务调用")
            return self._invoke(name, method, args, kwargs)

        return call

    async def _invoke(
        self, name: str, method: str | None, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        call_id = str(self._id)
        self._id += 1
        future = self._loop.create_future()
        self._pending[call_id] = future
        try:
            await self._transport.send(  # type: ignore[union-attr]
                {
                    "type": "call",
                    "id": call_id,
                    "name": name,
                    "method": method,
                    "args": list(args),
                    "kwargs": dict(kwargs),
                }
            )
        except Exception as error:
            self._pending.pop(call_id, None)
            raise RemoteClosed("远程调用") from error
        return await future

    def proxy(self, name: str) -> RemoteService:
        """返回 *name* 的远程服务代理（异步调用）。"""
        return RemoteService(self, name)

    # ------------------------------------------------------------------
    # 事件贯通
    # ------------------------------------------------------------------

    def send_event(self, event: str, *args: Any) -> None:
        """把事件发送到对端（fire-and-forget；断开时静默丢弃）。"""
        if self.closed:
            return
        if not has_running_loop():
            raise AsyncRequiredError("远程事件发送")

        async def deliver() -> None:
            try:
                await self._transport.send(  # type: ignore[union-attr]
                    {"type": "event", "event": event, "args": list(args)}
                )
            except Exception:  # noqa: BLE001, S110 - 对端断开时静默
                pass

        asyncio.create_task(deliver())

    def on_event(self, event: str, listener: Callable[..., Any]) -> Disposable:
        """注册对端事件监听器，返回 disposer。"""
        listeners = self._events.setdefault(event, [])
        listeners.append(listener)

        def undo() -> None:
            if listener in listeners:
                listeners.remove(listener)

        return undo

    def _dispatch_event(self, event: str, args: list[Any]) -> None:
        for listener in list(self._events.get(event, ())):
            try:
                result = listener(*args)
            except Exception:  # noqa: BLE001, S112 - 单个监听器失败不中断
                continue
            if inspect.isawaitable(result) and has_running_loop():
                asyncio.create_task(result)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def _resolve(self, call_id: Any, value: Any) -> None:
        future = self._pop(call_id)
        if future is not None and not future.done():
            future.set_result(value)

    def _pop(self, call_id: Any) -> asyncio.Future[Any] | None:
        if call_id is None:
            return None
        return self._pending.pop(str(call_id), None)

    async def close(self) -> None:
        """优雅关闭：发送 bye、停止接受连接并清理（幂等）。"""
        if self._transport is not None and not self._closed:
            try:
                await self._transport.send({"type": "bye"})
            except Exception:  # noqa: BLE001, S110 - 连接已断时跳过 bye
                pass
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None
        await self._shutdown()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _shutdown(self) -> None:
        """使所有未决调用失败并关闭传输（幂等）。"""
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(RemoteClosed())
        self._pending.clear()
        if self._transport is not None:
            await self._transport.close()
