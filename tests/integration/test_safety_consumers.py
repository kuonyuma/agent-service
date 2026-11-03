import asyncio
from io import StringIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from google.genai import types
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

import agent_service.core.agentic_loop as loop_module
import agent_service.ui.app as app_module
from agent_service.client.stream_message import StreamEvent, StreamResult
from agent_service.config.settings import SafetyConfig
from agent_service.core.agentic_loop import LoopEvent, LoopResult
from agent_service.core.coordinator import BLOCKED_MESSAGE, RunCoordinator
from agent_service.core.runner import AgentRunner
from agent_service.db import Database
from agent_service.safety.reviewer import ReviewResult
from agent_service.services.agent_api_service import AgentAPIService, PreparedRun


async def test_cli_replaces_answer_and_cancels_actual_prompt_application(monkeypatch):
    text = "待屏蔽的模型文本"
    output = StringIO()
    monkeypatch.setattr(app_module, "console", Console(file=output, width=80))
    request_call = types.FunctionCall(
        id="call-1", name="write_file", args={"path": "unused.txt", "content": "测试"}
    )

    async def model(**kwargs):
        yield StreamEvent(type="text", text=text)
        yield StreamEvent(
            type="message_done",
            result=StreamResult(
                contents=text,
                stop_reason="tool_use",
                usage={},
                function_calls=[request_call],
                raw_parts=[
                    types.Part.from_text(text=text),
                    types.Part(function_call=request_call),
                ],
            ),
        )

    monkeypatch.setattr(loop_module, "stream_message", model)
    with create_pipe_input() as pipe:
        session = PromptSession(input=pipe, output=DummyOutput())
        monkeypatch.setattr(app_module, "PromptSession", lambda: session)

        class Reviewer:
            async def check(self, text):
                while not session.app.is_running:
                    await asyncio.sleep(0)
                return ReviewResult("block", "violence")

        app = app_module.App(AgentRunner(plan_and_execute=False), Reviewer())
        app.coordinator.config = SafetyConfig(chunk_chars=1)
        async with asyncio.timeout(3):
            await app.run_turn("测试请求")
        assert not session.app.is_running
        assert app._active_live is None
        assert app._safety_stopped
        assert text not in str(app.contents)
        assert text not in output.getvalue()
        assert BLOCKED_MESSAGE in output.getvalue()


class BlockReviewer:
    def __init__(self):
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def check(self, text):
        self.started.set()
        await self.release.wait()
        return ReviewResult("block", "sexual")


def make_service(runner, reviewer, buffer=1):
    coordinator = RunCoordinator(
        cast(AgentRunner, runner), reviewer, SafetyConfig(chunk_chars=1)
    )
    service = AgentAPIService(
        cast(Database, SimpleNamespace(session_factory=None)),
        coordinator,
        stream_event_buffer_size=buffer,
    )
    service._finish_run = AsyncMock()
    return service


def prepared():
    return PreparedRun(
        run_id="run",
        conversation_id="conversation",
        message="问题",
        history=[],
        request_id="test",
    )


async def test_web_drops_queued_text_after_safety_latch_and_keeps_terminal():
    reviewer = BlockReviewer()

    class Runner:
        async def run_turn(self, **kwargs):
            for text in ("第一段", "排队原文", "后来原文"):
                yield LoopEvent(type="text.delta", text=text)
            yield LoopEvent(
                type="run.completed",
                result=LoopResult(
                    reason="completed",
                    history=[],
                    new_contents=[],
                    turns=1,
                ),
            )

    service = make_service(Runner(), reviewer)
    stream = service.stream_run(prepared())
    first = await anext(stream)
    assert first.type == "text.delta"
    await reviewer.started.wait()
    reviewer.release.set()
    # 让独立审核任务在网络消费者暂停期间封闭执行入口。
    await asyncio.sleep(0.01)
    async with asyncio.timeout(2):
        remaining = [event async for event in stream]
    assert [e.type for e in remaining] == ["safety.blocked", "run.failed"]
    assert remaining[0].data["code"] == "safety_blocked"
    assert "原文" not in str(remaining)
    args = cast(AsyncMock, service._finish_run).call_args.args
    assert args[2] == "failed"
    assert "原文" not in str(args[1].new_contents)


async def test_web_persists_tool_request_before_approval_and_clears_waiters():
    reviewer = BlockReviewer()
    saved = asyncio.Event()
    approval_cancelled = asyncio.Event()

    class Runner:
        async def run_turn(self, permission_check, **kwargs):
            yield LoopEvent(type="text.delta", text="待审核")
            from agent_service.core.agentic_loop import ToolApprovalRequest

            request = ToolApprovalRequest.create("call", "write_file", {})
            yield LoopEvent(
                type="tool.requested",
                tool_call_id="call",
                tool_name="write_file",
                requires_approval=True,
            )
            await permission_check(request)

    service = make_service(Runner(), reviewer, buffer=4)

    async def save(*args):
        saved.set()
        await service._register_approval_waiter("run", "call")

    async def approval(*args):
        assert saved.is_set()
        reviewer.release.set()
        try:
            await asyncio.Event().wait()
        finally:
            approval_cancelled.set()

    service._save_tool_requested = AsyncMock(side_effect=save)
    service._wait_for_approval = AsyncMock(side_effect=approval)
    async with asyncio.timeout(2):
        events = [event async for event in service.stream_run(prepared())]
    assert events[-1].type == "run.failed"
    assert approval_cancelled.is_set()
    assert not service._approval_waiters
    cast(AsyncMock, service._finish_run).assert_awaited_once()


async def test_web_disconnect_cancels_agent_and_inflight_review():
    agent_closed, review_closed, review_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Runner:
        async def run_turn(self, **kwargs):
            try:
                yield LoopEvent(type="text.delta", text="片段")
                await asyncio.Event().wait()
            finally:
                agent_closed.set()

    class Reviewer:
        async def check(self, text):
            review_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                review_closed.set()
            return ReviewResult("allow")

    service = make_service(Runner(), Reviewer())
    cancelled = AsyncMock()
    service._cancel_with_shield = cancelled
    stream = service.stream_run(prepared())
    await anext(stream)
    await review_started.wait()
    async with asyncio.timeout(2):
        await stream.aclose()
    assert agent_closed.is_set() and review_closed.is_set()
    cancelled.assert_awaited_once()
    assert not service._approval_waiters
