"""Agentic Loop 与流式客户端、工具执行器之间的集成测试。

这里只模拟模型返回，不访问真实 Gemini；真实 API 测试放在 external 目录。
"""

import pytest
from google.genai import types

import agent_service.core.agentic_loop
from agent_service.client.stream_message import StreamEvent, StreamResult
from agent_service.core.agentic_loop import query
from agent_service.tools.index import get_function_declarations


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


def create_plan_call(
    goal: str = "写入目标文件",
    step_title: str = "创建并写入文件",
) -> types.FunctionCall:
    return types.FunctionCall(
        name="update_plan",
        args={
            "action": "create",
            "goal": goal,
            "steps": [{"id": "step-1", "title": step_title}],
        },
    )


def complete_step_call(step_id: str = "step-1") -> types.FunctionCall:
    return types.FunctionCall(
        name="update_plan",
        args={
            "action": "update_step",
            "step_id": step_id,
            "status": "completed",
        },
    )


@pytest.mark.asyncio
async def test_query_plan_and_execute_full_lifecycle(monkeypatch, tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("初始代码", encoding="utf-8")
    target = tmp_path / "created.txt"

    read_call = types.FunctionCall(
        name="read_file",
        args={"path": str(source)},
    )
    plan_call = create_plan_call()
    write_call = types.FunctionCall(
        name="write_file",
        args={"path": str(target), "content": "工具已经执行"},
    )
    finish_step_call = complete_step_call("step-1")

    call_count = 0
    declared_tool_names_by_turn: list[set[str]] = []

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        tools_arg = kwargs.get("tools") or []
        names = {
            decl.name
            for group in tools_arg
            for decl in (group.function_declarations or [])
            if decl.name
        }
        declared_tool_names_by_turn.append(names)

        if call_count == 1:
            yield message_done(function_calls=[read_call, plan_call])
        elif call_count == 2:
            yield message_done(function_calls=[write_call, finish_step_call])
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
            tools=get_function_declarations(),
            permission_check=allow,
        )
    ]

    # 阶段一（Turn 1）仅暴露只读工具与 update_plan，阶段二（Turn 2）解锁 write_file
    assert "read_file" in declared_tool_names_by_turn[0]
    assert "update_plan" in declared_tool_names_by_turn[0]
    assert "write_file" not in declared_tool_names_by_turn[0]
    assert "write_file" in declared_tool_names_by_turn[1]

    assert [event.type for event in events] == [
        "tool.requested",
        "tool.requested",
        "tool.completed",
        "tool.completed",
        "plan.updated",
        "tool.requested",
        "tool.requested",
        "tool.completed",
        "tool.completed",
        "plan.updated",
        "run.completed",
    ]
    assert events[-1].result is not None
    assert events[-1].result.reason == "completed"
    assert events[-1].result.turns == 3
    assert events[-1].result.plan is not None
    assert events[-1].result.plan.is_completed()
    assert target.read_text(encoding="utf-8") == "工具已经执行"
    assert len(contents) == 1


@pytest.mark.asyncio
async def test_planning_phase_blocks_mutating_tool_before_plan_created(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "blocked_in_planning.txt"
    write_call = types.FunctionCall(
        name="write_file",
        args={"path": str(target), "content": "未规划不应写入"},
    )
    call_count = 0
    permission_called = False

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield message_done(function_calls=[write_call])
        else:
            yield message_done(
                contents="已知悉需先规划",
                raw_parts=[types.Part.from_text(text="已知悉需先规划")],
            )

    async def allow(request):
        nonlocal permission_called
        permission_called = True
        return True

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
            permission_check=allow,
        )
    ]

    assert not target.exists()
    assert not permission_called
    assert events[0].type == "tool.requested"
    assert events[0].requires_approval is False
    assert events[1].type == "tool.completed"
    assert events[1].tool_result is not None
    assert events[1].tool_result.is_error
    assert "规划阶段" in events[1].tool_result.content
    assert events[-1].type == "run.completed"


@pytest.mark.asyncio
async def test_dynamic_replan_and_unfinished_step_reminder(monkeypatch):
    call_count = 0

    async def fake_stream_message(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield message_done(
                function_calls=[
                    types.FunctionCall(
                        name="update_plan",
                        args={
                            "action": "create",
                            "goal": "初始计划",
                            "steps": [
                                {"id": "step-1", "title": "第一步"},
                            ],
                        },
                    )
                ]
            )
        elif call_count == 2:
            # 模拟触发动态重规划
            yield message_done(
                function_calls=[
                    types.FunctionCall(
                        name="update_plan",
                        args={
                            "action": "replan",
                            "goal": "重规划后的目标",
                            "steps": [
                                {
                                    "id": "step-1",
                                    "title": "第一步",
                                    "status": "completed",
                                },
                                {
                                    "id": "step-2",
                                    "title": "补充第二步",
                                    "status": "pending",
                                },
                            ],
                            "reason": "发现需要额外步骤",
                        },
                    )
                ]
            )
        elif call_count == 3:
            # 模拟模型在 step-2 仍为 pending 时试图直接结束，触发未完成步骤提醒
            yield message_done(
                contents="我准备提前结束",
                raw_parts=[types.Part.from_text(text="我准备提前结束")],
            )
        elif call_count == 4:
            # 收到系统提醒后将 step-2 标记为 completed
            yield message_done(
                function_calls=[complete_step_call("step-2")]
            )
        else:
            yield message_done(
                contents="全部步骤已完成",
                raw_parts=[types.Part.from_text(text="全部步骤已完成")],
            )

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    events = [event async for event in query(initial_contents(), tools=[])]
    plan_events = [event for event in events if event.type == "plan.updated"]

    assert len(plan_events) == 3
    assert plan_events[1].plan is not None
    assert plan_events[1].plan.revision == 2
    assert plan_events[1].plan.replan_reason == "发现需要额外步骤"
    assert events[-1].type == "run.completed"
    assert events[-1].result is not None
    assert events[-1].result.plan is not None
    assert events[-1].result.plan.is_completed()


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
            yield message_done(function_calls=[create_plan_call()])
        elif call_count == 2:
            yield message_done(function_calls=[write_call])
        elif call_count == 3:
            yield message_done(
                function_calls=[
                    types.FunctionCall(
                        name="update_plan",
                        args={
                            "action": "update_step",
                            "step_id": "step-1",
                            "status": "skipped",
                            "notes": "用户拒绝写入",
                        },
                    )
                ]
            )
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
    completed_write = [
        e
        for e in events
        if e.type == "tool.completed" and e.tool_name == "write_file"
    ]
    assert len(completed_write) == 1
    assert completed_write[0].tool_result is not None
    assert completed_write[0].tool_result.is_error
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
            plan_and_execute=False,
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

    events = [
        event
        async for event in query(
            initial_contents(),
            tools=[],
            plan_and_execute=False,
        )
    ]

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
