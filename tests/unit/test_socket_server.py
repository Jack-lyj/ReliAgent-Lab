from __future__ import annotations

import asyncio
import json
import socket
from typing import Any, cast

from kama_claude.core.bus.envelope import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    HandlerError,
)
from kama_claude.core.transport.socket_server import SocketServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return cast(int, s.getsockname()[1])


class MemoryWriter:
    """Small StreamWriter test double that preserves complete JSONL frames."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.drain_calls = 0

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        self.drain_calls += 1

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return ("127.0.0.1", 12345) if name == "peername" else default

    def messages(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.buffer.splitlines()]


# 功能：验证客户端断开后 SocketServer 调用 broadcaster.unsubscribe(writer) 清理订阅
# 设计：用内联 MockBroadcaster 捕获 unsubscribe 调用并设置 asyncio.Event，避免 sleep 轮询；
#       等待 Event 而非断言调用次数，确保时序正确性而不依赖竞态假设
async def test_broadcaster_unsubscribe_called_on_disconnect() -> None:
    unsubscribed = asyncio.Event()

    class MockBroadcaster:
        def unsubscribe(self, writer: object) -> None:
            unsubscribed.set()

    port = _free_port()
    server = SocketServer("127.0.0.1", port, broadcaster=MockBroadcaster())  # type: ignore[arg-type]
    await server.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()

        await asyncio.wait_for(unsubscribed.wait(), timeout=2.0)
    finally:
        await server.stop()


# 功能：验证不传入 broadcaster 时 SocketServer 仍可正常启动和停止（backward-compatible 默认值）
# 设计：直接实例化 SocketServer(host, port)（无 broadcaster），start/stop 不抛异常即为通过；
#       回归测试确保新参数的默认值 None 不破坏现有调用方
async def test_no_broadcaster_server_starts_and_stops() -> None:
    port = _free_port()
    server = SocketServer("127.0.0.1", port)
    await server.start()
    await server.stop()


async def test_handle_line_classifies_protocol_errors() -> None:
    server = SocketServer("127.0.0.1", 0)

    cases = [
        (b"{not-json}\n", PARSE_ERROR),
        (b'{"jsonrpc":"2.0","id":1}\n', INVALID_REQUEST),
        (
            b'{"jsonrpc":"2.0","id":"1","method":"missing","params":{}}\n',
            METHOD_NOT_FOUND,
        ),
    ]

    for request, expected_code in cases:
        writer = MemoryWriter()
        await server._handle_line(request, writer)  # type: ignore[arg-type]
        response = writer.messages()[0]
        assert response["error"]["code"] == expected_code
        assert writer.drain_calls == 1


async def test_handle_line_serializes_successful_result() -> None:
    server = SocketServer("127.0.0.1", 0)

    async def echo(params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params["value"]}

    server.register("test.echo", echo)
    writer = MemoryWriter()

    await server._handle_line(
        b'{"jsonrpc":"2.0","id":"r1","method":"test.echo","params":{"value":7}}\n',
        writer,  # type: ignore[arg-type]
    )

    assert writer.messages() == [
        {"jsonrpc": "2.0", "id": "r1", "result": {"echo": 7}},
    ]


async def test_handle_line_preserves_handler_error_contract() -> None:
    server = SocketServer("127.0.0.1", 0)

    async def rejected(_params: dict[str, Any]) -> None:
        raise HandlerError(-32042, "rejected", {"reason": "policy"})

    server.register("test.rejected", rejected)
    writer = MemoryWriter()

    await server._handle_line(
        b'{"jsonrpc":"2.0","id":"2","method":"test.rejected","params":{}}\n',
        writer,  # type: ignore[arg-type]
    )

    error = writer.messages()[0]["error"]
    assert error == {"code": -32042, "message": "rejected", "data": {"reason": "policy"}}


async def test_handle_line_hides_unexpected_handler_details() -> None:
    server = SocketServer("127.0.0.1", 0)

    async def broken(_params: dict[str, Any]) -> None:
        raise RuntimeError("secret internal detail")

    server.register("test.broken", broken)
    writer = MemoryWriter()

    await server._handle_line(
        b'{"jsonrpc":"2.0","id":"3","method":"test.broken","params":{}}\n',
        writer,  # type: ignore[arg-type]
    )

    error = writer.messages()[0]["error"]
    assert error["code"] == INTERNAL_ERROR
    assert error["message"] == "Internal error"
    assert "secret internal detail" not in json.dumps(error)
