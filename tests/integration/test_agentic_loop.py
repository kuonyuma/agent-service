"""Agentic Loop 与流式客户端、工具执行器之间的集成测试。

这里只模拟模型返回，不访问真实 Gemini；真实 API 测试放在 external 目录。
"""

import pytest
from google.genai import types

import agent_service.core.agentic_loop
from agent_service.client.stream_message import StreamEvent, StreamResult
from agent_service.core.agentic_loop import query


def message_done(
    *,
    contents: str = "",
    function_calls: list[types.FunctionCall] | None = None,
    raw_parts: list[types.Part] | None = None,
) -> StreamEvent:
    return StreamEvent(
        type="message_done",
        result=StreamResult(
            contents=contents,
            stop_reason="STOP",
            usage={},
            function_calls=function_calls or [],
            raw_parts=raw_parts or [],
        ),
    )


def initial_contents():
    return [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="请执行任务")],
        )
    ]


@pytest.mark.asyncio
async def test_query_executes_tool_and_completes(monkeypatch, tmp_path):
    target = tmp_path / "created.txt"
    write_call = types.FunctionCall(
        name="write_file",
        args={"path": str(target), "content": "工具已经执行"},
    )
    call_count = 0

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield message_done(function_calls=[write_call])
        else:
            yield message_done(
                contents="任务完成",
                raw_parts=[types.Part.from_text(text="任务完成")],
            )

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    contents = initial_contents()
    events = [event async for event in query(contents, tools=[])]

    assert [event.type for event in events] == [
        "tool_start",
        "tool_done",
        "turn_complete",
    ]
    assert events[-1].result is not None
    assert events[-1].result.reason == "completed"
    assert target.read_text(encoding="utf-8") == "工具已经执行"
    assert call_count == 2
    assert len(contents) == 4


@pytest.mark.asyncio
async def test_query_does_not_execute_denied_write_tool(monkeypatch, tmp_path):
    target = tmp_path / "should_not_exist.txt"
    write_call = types.FunctionCall(
        name="write_file",
        args={"path": str(target), "content": "不应写入"},
    )
    call_count = 0
    permission_calls = []

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield message_done(function_calls=[write_call])
        else:
            yield message_done(
                contents="已收到拒绝",
                raw_parts=[types.Part.from_text(text="已收到拒绝")],
            )

    async def deny(name, arguments):
        permission_calls.append((name, arguments))
        return False

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    events = [
        event
        async for event in query(
            initial_contents(),
            tools=[],
            permission_check=deny,
        )
    ]

    assert not target.exists()
    assert permission_calls == [("write_file", write_call.args)]
    assert events[-1].result is not None
    assert events[-1].result.reason == "completed"


@pytest.mark.asyncio
async def test_query_stops_after_ten_tool_turns(monkeypatch):
    call_count = 0

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        yield message_done(
            function_calls=[
                types.FunctionCall(name="not_registered", args={}),
            ]
        )

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    events = [event async for event in query(initial_contents(), tools=[])]

    assert call_count == 10
    assert events[-1].type == "turn_complete"
    assert events[-1].result is not None
    assert events[-1].result.reason == "max_turns"
