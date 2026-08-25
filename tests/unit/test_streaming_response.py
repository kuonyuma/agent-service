from typing import cast

import pytest
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope, Send

from agent_service.api.streaming import ManagedStreamingResponse


def _scope() -> Scope:
    return cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/runs",
            "raw_path": b"/runs",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "state": {},
        },
    )


async def _receive() -> Message:
    return {"type": "http.disconnect"}


@pytest.mark.asyncio
async def test_response_cleans_up_when_response_start_fails_before_first_frame():
    entered = False
    cleaned = False

    async def content():
        nonlocal entered
        entered = True
        yield b"data: never\n\n"

    async def cleanup() -> None:
        nonlocal cleaned
        cleaned = True

    async def failing_send(message: Message) -> None:
        raise RuntimeError("客户端已断开")

    response = ManagedStreamingResponse(
        content(),
        on_incomplete=cleanup,
        media_type="text/event-stream",
    )

    with pytest.raises(RuntimeError, match="客户端已断开"):
        await response(
            _scope(),
            cast(Receive, _receive),
            cast(Send, failing_send),
        )

    assert cleaned
    assert not entered


@pytest.mark.asyncio
async def test_response_does_not_cancel_a_fully_consumed_stream():
    cleaned = False
    messages: list[Message] = []

    async def content():
        yield b"data: done\n\n"

    async def cleanup() -> None:
        nonlocal cleaned
        cleaned = True

    async def send(message: Message) -> None:
        messages.append(message)

    response = ManagedStreamingResponse(
        content(),
        on_incomplete=cleanup,
        media_type="text/event-stream",
    )
    await response(_scope(), cast(Receive, _receive), cast(Send, send))

    assert not cleaned
    assert messages[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }


@pytest.mark.asyncio
async def test_response_cleans_up_after_disconnect_between_sse_frames():
    cleaned = False
    sent_bodies: list[bytes] = []

    async def content():
        yield b"event: text.delta\ndata: first\n\n"
        yield b"event: text.delta\ndata: second\n\n"

    async def cleanup() -> None:
        nonlocal cleaned
        cleaned = True

    async def disconnecting_send(message: Message) -> None:
        if message["type"] != "http.response.body":
            return
        body = message.get("body", b"")
        if sent_bodies:
            raise OSError("客户端在首帧后断开")
        sent_bodies.append(body)

    response = ManagedStreamingResponse(
        content(),
        on_incomplete=cleanup,
        media_type="text/event-stream",
    )

    with pytest.raises(ClientDisconnect):
        await response(
            _scope(),
            cast(Receive, _receive),
            cast(Send, disconnecting_send),
        )

    assert sent_bodies == [b"event: text.delta\ndata: first\n\n"]
    assert cleaned
