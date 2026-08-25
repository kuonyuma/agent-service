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

    async def allow(request):
        return True

    contents = initial_contents()
    events = [
        event
        async for event in query(
            contents,
            tools=[],
            permission_check=allow,
        )
    ]

    assert [event.type for event in events] == [
        "tool.requested",
        "tool.completed",
        "run.completed",
    ]
    assert events[-1].result is not None
    assert events[-1].result.reason == "completed"
    assert events[-1].result.turns == 2
    assert len(events[-1].result.new_contents) == 3
    assert events[0].tool_call_id
    assert events[0].tool_call_id == events[1].tool_call_id
    assert events[1].tool_result is not None
    assert not events[1].tool_result.is_error
    assert target.read_text(encoding="utf-8") == "工具已经执行"
    assert call_count == 2
    assert len(contents) == 1
    assert len(events[-1].result.history) == 4


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

    async def deny(request):
        permission_calls.append((request.tool_name, request.arguments))
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
    assert [event.type for event in events] == [
        "tool.requested",
        "tool.completed",
        "run.completed",
    ]
    assert events[1].tool_result is not None
    assert events[1].tool_result.is_error
    assert events[-1].result is not None
    assert events[-1].result.reason == "completed"


@pytest.mark.asyncio
async def test_rejected_write_does_not_reject_read_tool_in_same_batch(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "source.txt"
    source.write_text("只读调用成功", encoding="utf-8")
    target = tmp_path / "blocked.txt"
    call_count = 0

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield message_done(
                function_calls=[
                    types.FunctionCall(
                        name="read_file",
                        args={"path": str(source)},
                    ),
                    types.FunctionCall(
                        name="write_file",
                        args={"path": str(target), "content": "禁止写入"},
                    ),
                ]
            )
        else:
            yield message_done(
                contents="处理完成",
                raw_parts=[types.Part.from_text(text="处理完成")],
            )

    async def reject_write(request):
        assert request.tool_name == "write_file"
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
            permission_check=reject_write,
        )
    ]
    completed = [event for event in events if event.type == "tool.completed"]

    assert len(completed) == 2
    assert completed[0].tool_name == "read_file"
    assert completed[0].tool_result is not None
    assert not completed[0].tool_result.is_error
    assert completed[1].tool_name == "write_file"
    assert completed[1].tool_result is not None
    assert completed[1].tool_result.is_error
    assert not target.exists()


@pytest.mark.asyncio
async def test_query_denies_write_tool_without_permission_callback(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "must_not_exist.txt"
    write_call = types.FunctionCall(
        name="write_file",
        args={"path": str(target), "content": "不应写入"},
    )
    call_count = 0

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield message_done(function_calls=[write_call])
        else:
            yield message_done(
                contents="已拒绝未授权调用",
                raw_parts=[types.Part.from_text(text="已拒绝未授权调用")],
            )

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    events = [event async for event in query(initial_contents(), tools=[])]

    assert not target.exists()
    assert events[1].type == "tool.completed"
    assert events[1].tool_result is not None
    assert events[1].tool_result.is_error
    assert events[-1].type == "run.completed"


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
    assert events[-1].type == "run.failed"
    assert events[-1].result is not None
    assert events[-1].result.reason == "max_turns"


@pytest.mark.asyncio
async def test_query_stops_when_function_call_has_no_name(monkeypatch):
    async def fake_stream_message(*args, **kwargs):
        yield message_done(function_calls=[types.FunctionCall(args={})])

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    events = [event async for event in query(initial_contents(), tools=[])]

    assert [event.type for event in events] == ["run.failed"]
    assert events[-1].result is not None
    assert events[-1].result.reason == "error"
    assert events[-1].result.error is not None
