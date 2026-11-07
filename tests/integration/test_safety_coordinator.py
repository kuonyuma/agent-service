import asyncio
from contextlib import aclosing
from dataclasses import replace
from typing import Any, ClassVar, Literal, cast
from unittest.mock import AsyncMock

import pytest
from google.genai import types

import agent_service.adapters.gemini.model as loop_module
from agent_service.adapters.collaboration.manager import SubagentManager
from agent_service.adapters.security.coordinator import BLOCKED_MESSAGE, RunCoordinator
from agent_service.adapters.security.reviewer import ReviewResult
from agent_service.config.settings import SafetyConfig
from agent_service.errors import SafetyBlockedError, SafetyReviewError
from agent_service.kernel.dispatch.approval import ToolApprovalRequest
from agent_service.kernel.dispatch.base import Tool, ToolResult
from agent_service.kernel.dispatch.executor import execute_tools
from agent_service.kernel.model.port import StreamEvent, StreamResult
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.runtime.events import LoopEvent, LoopResult
from agent_service.kernel.runtime.runner import AgentRunner
from tests.support import create_runner

FAST = SafetyConfig(
    chunk_chars=1,
    interval_seconds=0.01,
    timeout_seconds=1,
    context_chars=2,
    max_buffer_chars=20,
)


class Reviewer:
    verdict: Literal["allow", "block", "error"]

    def __init__(self, verdict: Literal["allow", "block", "error"] = "allow"):
        self.verdict = verdict
        self.texts = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.closed = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def check(self, text):
        self.texts.append(text)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            return ReviewResult(
                self.verdict,
                "violence" if self.verdict == "block" else "none",
                {"input_len": 3, "output_len": 1},
            )
        finally:
            self.active -= 1
            self.closed.set()


def completed(text):
    content = types.Content(role="model", parts=[types.Part.from_text(text=text)])
    return LoopEvent(
        type="run.completed",
        result=LoopResult(
            reason="completed",
            history=[content],
            new_contents=[content],
            turns=1,
            usage={"input_len": 10, "output_len": 2},
        ),
    )


class TextRunner:
    def __init__(self, chunks=("甲", "乙", "丙")):
        self.chunks = chunks
        self.closed = asyncio.Event()

    async def run_turn(self, **kwargs):
        try:
            for text in self.chunks:
                yield LoopEvent(type="text.delta", text=text)
            yield completed("".join(self.chunks))
        finally:
            self.closed.set()


async def collect(coordinator, **kwargs):
    async with asyncio.timeout(2):
        return [event async for event in coordinator.run_turn([], "测试", **kwargs)]


async def test_pass_streams_immediately_reviews_new_text_serially_and_waits_for_tail():
    reviewer = Reviewer()
    reviewer.release.clear()
    runner = TextRunner()
    coordinator = RunCoordinator(cast(AgentRunner, runner), reviewer, FAST)
    async with aclosing(coordinator.run_turn([], "测试")) as stream:
        event = await anext(stream)
        assert event.type == "text.delta"
        await reviewer.started.wait()
        rest = asyncio.create_task(_remaining(stream))
        await runner.closed.wait()
        assert not rest.done()
        reviewer.release.set()
        events = [event, *await rest]
    assert [e.type for e in events] == ["text.delta"] * 3 + ["run.completed"]
    assert reviewer.texts == ["甲", "甲乙丙"]
    assert reviewer.max_active == 1
    assert events[-1].result.usage == {"input_len": 16, "output_len": 4}
    assert events[-1].result.safety_usage == {"input_len": 6, "output_len": 2}


async def _remaining(stream):
    return [event async for event in stream]


async def test_tail_block_overrides_agent_success_and_removes_raw_history():
    reviewer = Reviewer("block")
    config = replace(FAST, chunk_chars=20)
    events = await collect(
        RunCoordinator(cast(AgentRunner, TextRunner(("风险原文",))), reviewer, config)
    )
    assert [e.type for e in events] == ["text.delta", "safety.blocked", "run.failed"]
    result = events[-1].result
    assert isinstance(result.error, SafetyBlockedError)
    assert "风险原文" not in str(result.history)
    assert BLOCKED_MESSAGE in str(result.history)
    assert events[-2].safety_category == "violence"


class ApprovalRunner:
    def __init__(self):
        self.started_tool = False
        self.closed = asyncio.Event()

    async def run_turn(self, permission_check, run_control, **kwargs):
        try:
            yield LoopEvent(type="text.delta", text="待检查文本")
            request = ToolApprovalRequest.create("call-1", "write_file", {})
            yield LoopEvent(
                type="tool.requested",
                tool_call_id="call-1",
                tool_name="write_file",
                requires_approval=True,
            )
            allowed = await permission_check(request)
            if allowed:
                run_control.ensure_running()
                self.started_tool = True
            yield completed("结束")
        finally:
            self.closed.set()


@pytest.mark.parametrize("simultaneous_approval", [False, True])
async def test_block_interrupts_approval_and_wins_after_latch(simultaneous_approval):
    reviewer = Reviewer("block")
    reviewer.release.clear()
    runner = ApprovalRunner()
    waiting, cancelled = asyncio.Event(), asyncio.Event()

    async def permission(request):
        waiting.set()
        try:
            await (
                reviewer.release.wait()
                if simultaneous_approval
                else asyncio.Event().wait()
            )
            # 给审核任务机会先封闭执行入口，模拟批准与拦截同时到达。
            await asyncio.sleep(0)
            return True
        finally:
            cancelled.set()

    task = asyncio.create_task(
        collect(
            RunCoordinator(cast(AgentRunner, runner), reviewer, FAST),
            permission_check=permission,
        )
    )
    await waiting.wait()
    reviewer.release.set()
    events = await task
    assert cancelled.is_set() and runner.closed.is_set()
    assert not runner.started_tool
    assert [e.type for e in events][-2:] == ["safety.blocked", "run.failed"]


@pytest.mark.parametrize("failure", ["timeout", "exception", "invalid", "overflow"])
async def test_review_failure_stops_run(failure):
    reviewer = Reviewer("error" if failure == "invalid" else "allow")
    if failure == "timeout":
        reviewer.release.clear()
    if failure == "exception":
        reviewer.check = AsyncMock(side_effect=ValueError("不应暴露原文"))
    config = replace(FAST, timeout_seconds=0.01)
    runner = TextRunner(
        ("超出缓冲区上限的文本" * 4,) if failure == "overflow" else ("片段",)
    )
    events = await collect(RunCoordinator(cast(AgentRunner, runner), reviewer, config))
    assert isinstance(events[-1].result.error, SafetyReviewError)
    assert events[-2].safety_code == "safety_review_failed"
    assert "不应暴露原文" not in str(events)


async def test_time_trigger_runs_while_model_has_not_finished():
    reviewer = Reviewer("block")

    class SlowRunner:
        async def run_turn(self, **kwargs):
            yield LoopEvent(type="text.delta", text="短片段")
            await asyncio.Event().wait()

    events = await collect(
        RunCoordinator(
            cast(AgentRunner, SlowRunner()), reviewer, replace(FAST, chunk_chars=20)
        )
    )
    assert events[-1].type == "run.failed"
    assert reviewer.texts == ["短片段"]


async def test_consumer_cancel_closes_both_branches():
    reviewer = Reviewer()
    reviewer.release.clear()
    runner = TextRunner()
    stream = RunCoordinator(cast(AgentRunner, runner), reviewer, FAST).run_turn(
        [], "测试"
    )
    await anext(stream)
    await reviewer.started.wait()
    await stream.aclose()
    assert reviewer.closed.is_set() and runner.closed.is_set()
    assert not [
        t
        for t in asyncio.all_tasks()
        if t.get_name() in {"agent-producer", "safety-reviewer"}
    ]


class RecordingTool(Tool):
    name = "record"
    description = "测试记录工具"
    input_schema: ClassVar[dict[str, Any]] = {}
    read_only = False

    def __init__(self):
        self.calls = 0

    async def run(self, parameter):
        self.calls += 1
        return ToolResult(content="已完成", is_error=False)


async def test_execution_gate_checks_after_waiting_for_tool_slot():
    started, release = asyncio.Event(), asyncio.Event()

    class SlowTool(RecordingTool):
        async def run(self, parameter):
            self.calls += 1
            started.set()
            await release.wait()
            return ToolResult(content="已完成", is_error=False)

    tool, control = SlowTool(), RunControl()
    task = asyncio.create_task(
        execute_tools(
            [
                types.FunctionCall(id=call_id, name="record", args={})
                for call_id in ("first", "queued")
            ],
            approved_tool_call_ids=frozenset({"first", "queued"}),
            tool_registry={"record": tool},
            run_control=control,
            max_concurrency=1,
        )
    )
    await started.wait()
    control.stopped = True
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tool.calls == 1


async def test_inflight_buffer_overflow_interrupts_reviewer_without_waiting_for_timeout():
    reviewer = Reviewer()
    reviewer.release.clear()
    runner = TextRunner(("甲", "乙" * 8, "丙" * 8))
    config = replace(FAST, timeout_seconds=30, max_buffer_chars=10)
    async with asyncio.timeout(1):
        events = await collect(
            RunCoordinator(cast(AgentRunner, runner), reviewer, config)
        )
    assert isinstance(events[-1].result.error, SafetyReviewError)
    assert reviewer.closed.is_set()


async def test_real_loop_checkpoint_keeps_complete_tool_pairs_and_removes_text(
    monkeypatch,
):
    calls = 0
    tool = RecordingTool()
    reviewer = Reviewer()

    async def model(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            fc = types.FunctionCall(id="call", name="record", args={})
            yield StreamEvent(
                type="message_done",
                result=StreamResult(
                    contents="",
                    stop_reason="tool_use",
                    usage={},
                    function_calls=[fc],
                    raw_parts=[types.Part(function_call=fc)],
                ),
            )
        else:
            reviewer.verdict = "block"
            yield StreamEvent(type="text", text="应移除原文")
            await asyncio.Event().wait()

    monkeypatch.setattr(loop_module, "stream_message", model)
    runner = create_runner(tool_registry={"record": tool}, plan_and_execute=False)

    async def allow(request):
        return True

    events = await collect(
        RunCoordinator(runner, reviewer, FAST), permission_check=allow
    )
    result = events[-1].result
    assert tool.calls == 1
    assert "应移除原文" not in str(result.history)
    new = result.new_contents
    assert new[0].parts[0].function_call.id == "call"
    assert new[1].parts[0].function_response.id == "call"
    assert "已完成" in str(new[1])


async def test_child_approval_cancel_closes_nested_generator_and_shares_stop_gate():
    forwarded, closed = asyncio.Event(), asyncio.Event()
    parent = RunControl()

    async def child_query(**kwargs):
        child_control = kwargs["run_control"]
        assert child_control.parent is parent
        try:
            yield LoopEvent(type="tool.requested", tool_call_id="child", requires_approval=True)
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def forward(event):
        forwarded.set()
        await asyncio.Event().wait()

    manager = SubagentManager({}, query_runner=child_query, event_forwarder=forward, run_control=parent)
    await manager.spawn(role="worker", task="测试", background=True)
    await forwarded.wait()
    parent.stopped = True
    child_control = RunControl(parent=parent)
    with pytest.raises(asyncio.CancelledError):
        child_control.ensure_running()
    await manager.close()
    assert closed.is_set()
