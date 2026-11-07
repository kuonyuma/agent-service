"""通过模型端口直接驱动内核，不依赖 Gemini、配置单例或真实外部工具。"""

import asyncio
from collections.abc import AsyncGenerator
from copy import deepcopy
from typing import Any, ClassVar

import pytest
from google.genai import types

from agent_service.adapters.collaboration.manager import SubagentManager
from agent_service.kernel.dispatch.approval import ToolApprovalRequest
from agent_service.kernel.dispatch.base import Tool, ToolResult
from agent_service.kernel.dispatch.executor import execute_tools
from agent_service.kernel.model.port import StreamEvent, StreamResult
from agent_service.kernel.runtime.bindings import RunBindings
from agent_service.kernel.runtime.events import LoopEvent, LoopResult
from agent_service.kernel.runtime.loop import query, turn_limit
from agent_service.kernel.runtime.runner import AgentRunner
from agent_service.kernel.strategy.plan import ExecutionPlan


def response(*calls: types.FunctionCall, text: str = "") -> StreamResult:
    return StreamResult(
        contents=text,
        stop_reason="STOP",
        usage={"input_len": 3, "output_len": 2},
        function_calls=list(calls),
    )


class ScriptedModel:
    def __init__(self, *responses: StreamResult) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.closed_streams = 0
        self.closed = False

    async def stream(
        self,
        *,
        contents,
        system_prompt,
        max_tokens,
        tools=None,
    ) -> AsyncGenerator[StreamEvent]:
        self.requests.append(
            deepcopy(
                {
                    "contents": contents,
                    "system_prompt": system_prompt,
                    "max_tokens": max_tokens,
                    "tools": tools,
                }
            )
        )
        try:
            result = self.responses.pop(0)
            if result.contents:
                yield StreamEvent(type="text", text=result.contents)
            yield StreamEvent(type="message_done", result=result)
        finally:
            self.closed_streams += 1

    async def close(self) -> None:
        self.closed = True


class ProbeTool(Tool):
    name = "probe"
    description = "仅记录测试调用，不接触文件或进程。"
    input_schema: ClassVar[dict[str, Any]] = {"type": "OBJECT", "properties": {}}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        self.calls.append(deepcopy(parameter))
        return ToolResult("完成", is_error=False)


class MutatingProbeTool(ProbeTool):
    read_only = False


def runner_for(model, tool=None, **kwargs):
    registry = {tool.name: tool} if tool is not None else {}
    return AgentRunner(
        model=model,
        tool_registry=registry,
        system_prompt="测试系统提示词",
        **kwargs,
    )


async def approve(request: ToolApprovalRequest) -> bool:
    return True


async def test_runner_uses_only_injected_model_and_preserves_input_history():
    history = [types.Content(role="user", parts=[types.Part.from_text(text="旧问题")])]
    original = deepcopy(history)
    model = ScriptedModel(response(text="新回答"))
    async with runner_for(model, max_tokens=123) as runner:
        events = [event async for event in runner.run_turn(history, "新问题")]
    final = events[-1].result

    assert final is not None and final.reason == "completed"
    assert final.usage == {"input_len": 3, "output_len": 2}
    assert model.requests[0]["system_prompt"] == "测试系统提示词"
    assert model.requests[0]["max_tokens"] == 123
    assert model.closed_streams == 1 and model.closed
    assert history == original
    final.history[0].parts = []
    assert history == original


@pytest.mark.parametrize(
    "restriction", [{"tools": []}, {"allowed_tool_names": frozenset()}]
)
async def test_undeclared_or_disallowed_tools_never_execute(restriction):
    tool = ProbeTool()
    model = ScriptedModel(
        response(types.FunctionCall(name="probe")), response(text="结束")
    )
    events = [
        event
        async for event in runner_for(model, tool, **restriction).run_turn([], "测试")
    ]

    completed = [event for event in events if event.type == "tool.completed"]
    assert len(completed) == 1
    assert completed[0].tool_result is not None and completed[0].tool_result.is_error
    assert not tool.calls
    assert model.requests[0]["tools"] == []


async def test_mutated_approval_arguments_fail_closed():
    tool = MutatingProbeTool()
    model = ScriptedModel(
        response(types.FunctionCall(name="probe", args={"nested": {"value": "原始"}})),
        response(text="结束"),
    )

    async def tamper(request: ToolApprovalRequest) -> bool:
        request.arguments["nested"]["value"] = "篡改"
        return True

    events = [
        event async for event in runner_for(model, tool).run_turn([], "测试", tamper)
    ]

    assert not tool.calls
    completed = next(event for event in events if event.type == "tool.completed")
    assert completed.tool_result is not None and completed.tool_result.is_error


async def test_approval_failure_does_not_persist_an_unpaired_function_call():
    tool = MutatingProbeTool()
    model = ScriptedModel(response(types.FunctionCall(name="probe")))

    async def broken_approval(request: ToolApprovalRequest) -> bool:
        raise RuntimeError("审批通道不可用")

    events = [
        event
        async for event in runner_for(model, tool).run_turn([], "测试", broken_approval)
    ]
    final = events[-1].result

    assert final is not None and final.reason == "error"
    assert final.error is not None and "审批通道不可用" in str(final.error)
    assert not tool.calls
    assert len(final.history) == 1 and final.history[0].role == "user"
    assert not final.new_contents
    assert not any(
        part.function_call or part.function_response
        for content in final.history
        for part in content.parts or []
    )


async def test_event_consumer_cannot_rewrite_pending_call_arguments():
    tool = MutatingProbeTool()
    model = ScriptedModel(
        response(types.FunctionCall(name="probe", args={"nested": {"value": "原始"}})),
        response(text="结束"),
    )
    async for event in runner_for(model, tool).run_turn([], "测试", approve):
        if event.type == "tool.requested":
            event.tool_arguments["nested"]["value"] = "篡改"
    assert tool.calls == [{"nested": {"value": "原始"}}]


async def test_duplicate_and_missing_ids_match_model_parts_and_function_responses():
    tool = ProbeTool()
    signed = types.Part(
        function_call=types.FunctionCall(id="重复", name="probe"),
        thought_signature="保留签名".encode(),
    )
    first = response()
    first.raw_parts = [
        signed,
        types.Part(function_call=types.FunctionCall(id="重复", name="probe")),
        types.Part(function_call=types.FunctionCall(name="probe")),
    ]
    model = ScriptedModel(
        first,
        response(types.FunctionCall(id="重复", name="probe")),
        response(text="结束"),
    )

    events = [event async for event in runner_for(model, tool).run_turn([], "测试")]
    final = events[-1].result
    assert final is not None
    calls = [
        part.function_call
        for content in final.history
        for part in content.parts or []
        if part.function_call is not None
    ]
    replies = [
        part.function_response
        for content in final.history
        for part in content.parts or []
        if part.function_response is not None
    ]
    assert len(calls) == len(replies) == 4
    assert len({call.id for call in calls}) == 4
    assert [call.id for call in calls] == [reply.id for reply in replies]
    assert all(call.id for call in calls)
    assert any(
        part.thought_signature == "保留签名".encode()
        for content in final.history
        for part in content.parts or []
    )


async def test_turn_limit_is_hard_even_if_caller_requests_more():
    tool = ProbeTool()
    model = ScriptedModel(
        *[response(types.FunctionCall(name="probe")) for _ in range(12)]
    )

    events = [
        event
        async for event in runner_for(model, tool, max_turns=100).run_turn([], "测试")
    ]

    assert len(model.requests) == len(tool.calls) == 10
    assert events[-1].result is not None
    assert events[-1].result.reason == "max_turns"
    assert events[-1].result.turns == 10


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_turn_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="正整数"):
        turn_limit(limit)


async def test_final_summary_turn_blocks_even_hallucinated_tools():
    tool = ProbeTool()
    model = ScriptedModel(response(types.FunctionCall(name="probe")))
    events = [
        event
        async for event in query(
            [types.Content(role="user", parts=[types.Part.from_text(text="测试")])],
            [types.Tool(function_declarations=[tool.to_function_declaration()])],
            model=model,
            tool_registry={"probe": tool},
            system_prompt="测试",
            max_turns=1,
            graceful_final_turn=True,
        )
    ]

    assert not tool.calls
    assert model.requests[0]["tools"] == []
    completed = next(event for event in events if event.type == "tool.completed")
    assert completed.tool_result is not None and completed.tool_result.is_error
    assert "最终总结轮" in completed.tool_result.content


async def test_closing_runner_stream_closes_model_stream_and_run_bindings():
    model = ScriptedModel(response(text="一段文字"))
    cleaned = False

    async def cleanup():
        nonlocal cleaned
        cleaned = True

    def bind(**kwargs):
        return RunBindings(kwargs["registry"], cleanup=cleanup)

    stream = runner_for(model, binding_factory=bind).run_turn([], "测试")
    assert (await anext(stream)).type == "text.delta"
    await stream.aclose()

    assert model.closed_streams == 1
    assert cleaned


async def test_missing_model_completion_becomes_one_failed_terminal():
    class IncompleteModel(ScriptedModel):
        async def stream(self, **kwargs):
            yield StreamEvent(type="text", text="未完成")

    model = IncompleteModel()
    events = [event async for event in runner_for(model).run_turn([], "测试")]

    assert [event.type for event in events] == ["text.delta", "run.failed"]
    assert events[-1].result is not None
    assert "完成结果" in str(events[-1].result.error)


async def test_single_tool_exception_keeps_all_responses():
    class BrokenTool(ProbeTool):
        name = "broken"

        async def run(self, parameter):
            raise ValueError("测试异常")

    ok = ProbeTool()
    broken = BrokenTool()
    result = await execute_tools(
        [
            types.FunctionCall(id="a", name="broken"),
            types.FunctionCall(id="b", name="probe"),
        ],
        tool_registry={"broken": broken, "probe": ok},
    )

    assert len(result.executions) == 2
    assert result.executions[0].result.is_error
    assert not result.executions[1].result.is_error
    assert len(result.content.parts or []) == 2


async def test_executor_cancellation_joins_every_started_task():
    started = [asyncio.Event(), asyncio.Event()]
    stopped = [asyncio.Event(), asyncio.Event()]

    class WaitingTool(ProbeTool):
        async def run(self, parameter):
            index = parameter["index"]
            started[index].set()
            try:
                await asyncio.Event().wait()
                return ToolResult("不应到达", is_error=False)
            finally:
                stopped[index].set()

    tool = WaitingTool()
    task = asyncio.create_task(
        execute_tools(
            [
                types.FunctionCall(id=str(i), name="probe", args={"index": i})
                for i in range(2)
            ],
            tool_registry={"probe": tool},
        )
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started)), timeout=2
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(event.is_set() for event in stopped)


async def test_child_verification_does_not_clear_parent_or_sibling_edits():
    plan = ExecutionPlan(phase="executing")
    plan.record_edit("parent.py")

    async def child_query(**kwargs):
        for name, path in (
            ("write_file", "child.py"),
            ("read_file", "child.py"),
            ("run_quality_check", ""),
        ):
            yield LoopEvent(
                type="tool.completed",
                tool_name=name,
                tool_arguments={"path": path},
                tool_result=ToolResult("完成", is_error=False),
            )
        yield LoopEvent(
            type="run.completed",
            result=LoopResult(
                reason="completed",
                history=[],
                new_contents=[],
                turns=1,
                usage={},
            ),
        )

    manager = SubagentManager({}, query_runner=child_query, parent_plan=plan)
    try:
        await manager.spawn(role="worker", task="验证子任务")
    finally:
        await manager.close()
    assert plan.unverified_edits == ["parent.py", "child.py"]
