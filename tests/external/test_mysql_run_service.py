import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from google.genai import types
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from starlette.types import Message as ASGIMessage
from starlette.types import Receive, Scope, Send

from agent_service.api.app import create_app
from agent_service.api.streaming import ManagedStreamingResponse
from agent_service.core.agentic_loop import (
    LoopEvent,
    LoopResult,
    ToolApprovalRequest,
)
from agent_service.core.runner import AgentRunner
from agent_service.db import AgentRun, Conversation, ToolCall
from agent_service.errors import (
    AgentRuntimeError,
    ApprovalConflictError,
    ConversationBusyError,
)
from agent_service.services.agent_api_service import (
    AgentAPIService,
    PreparedRun,
)
from agent_service.tools.base import ToolResult


def _completed_result(history: list[types.Content], answer: str) -> LoopResult:
    model_content = types.Content(
        role="model",
        parts=[types.Part.from_text(text=answer)],
    )
    return LoopResult(
        reason="completed",
        history=[*history, model_content],
        new_contents=[model_content],
        turns=1,
        usage={"input_len": 8, "output_len": 3},
    )


class CompletingRunner:
    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        yield LoopEvent(type="text.delta", text="完成", turn=1)
        yield LoopEvent(
            type="run.completed",
            turn=1,
            result=_completed_result(history, "完成"),
        )


class ToolRunner:
    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        yield LoopEvent(
            type="tool.requested",
            turn=1,
            tool_call_id="call-1",
            tool_name="read_file",
            tool_arguments={"path": "README.md"},
        )
        yield LoopEvent(
            type="tool.completed",
            turn=1,
            tool_call_id="call-1",
            tool_name="read_file",
            tool_arguments={"path": "README.md"},
            tool_result=ToolResult(content="读取完成", is_error=False),
        )
        yield LoopEvent(
            type="run.completed",
            turn=1,
            result=_completed_result(history, "工具执行完成"),
        )


class FailingRunner:
    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        error = AgentRuntimeError("模拟模型失败")
        yield LoopEvent(
            type="run.failed",
            turn=1,
            result=LoopResult(
                reason="error",
                history=list(history),
                new_contents=[],
                turns=1,
                usage={"input_len": 2, "output_len": 0},
                error=error,
            ),
        )


class PausedRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        self.started.set()
        await self.release.wait()
        yield LoopEvent(
            type="run.completed",
            turn=1,
            result=_completed_result(history, "解除等待"),
        )


class SlowAfterDeltaRunner:
    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        yield LoopEvent(type="text.delta", text="第一段", turn=1)
        await asyncio.sleep(0.02)
        yield LoopEvent(
            type="run.completed",
            turn=1,
            result=_completed_result(history, "完整回答"),
        )


class HangingRunner:
    def __init__(self) -> None:
        self.never = asyncio.Event()

    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        yield LoopEvent(
            type="tool.requested",
            turn=1,
            tool_call_id="pending-call",
            tool_name="read_file",
            tool_arguments={"path": "README.md"},
        )
        await self.never.wait()


class ApprovalRunner:
    def __init__(self) -> None:
        self.executed = False

    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        request = ToolApprovalRequest.create(
            "approval-call",
            "write_file",
            {"path": "notes.txt", "content": "审批内容"},
        )
        yield LoopEvent(
            type="tool.requested",
            turn=1,
            tool_call_id=request.call_id,
            tool_name=request.tool_name,
            tool_arguments=request.arguments,
            requires_approval=True,
            approval_request_hash=request.request_hash,
        )
        allowed = await permission_check(request) if permission_check else False
        self.executed = allowed
        result = ToolResult(
            content="工具已执行" if allowed else "工具未获批准",
            is_error=not allowed,
        )
        yield LoopEvent(
            type="tool.completed",
            turn=1,
            tool_call_id=request.call_id,
            tool_name=request.tool_name,
            tool_arguments=request.arguments,
            tool_result=result,
        )
        yield LoopEvent(
            type="run.completed",
            turn=1,
            result=_completed_result(history, "审批流程完成"),
        )


def _service(
    mysql_database,
    runner,
    *,
    timeout: float = 10,
    approval_timeout: float = 2,
) -> AgentAPIService:
    return AgentAPIService(
        mysql_database,
        cast(AgentRunner, runner),
        run_timeout_seconds=timeout,
        approval_timeout_seconds=approval_timeout,
        approval_poll_interval_seconds=0.01,
    )


async def test_mysql_run_persists_messages_and_releases_claim(mysql_database):
    service = _service(mysql_database, CompletingRunner())
    conversation = await service.create_conversation("MySQL 完成测试")

    prepared = await service.prepare_run(conversation.id, "开始")
    events = [event async for event in service.stream_run(prepared)]

    run = await service.get_run(prepared.run_id)
    saved_conversation = await service.get_conversation(conversation.id)
    messages = await service.list_messages(conversation.id)
    assert [event.type for event in events] == ["text.delta", "run.completed"]
    assert run.status == "completed"
    assert run.turn_count == 1
    assert run.input_tokens == 8
    assert saved_conversation.active_run_id is None
    assert [(message.sequence_no, message.kind) for message in messages] == [
        (1, "user_message"),
        (2, "model_message"),
    ]


async def test_mysql_tool_audit_preserves_is_error(mysql_database):
    service = _service(mysql_database, ToolRunner())
    conversation = await service.create_conversation("工具审计")
    prepared = await service.prepare_run(conversation.id, "读取文件")

    _ = [event async for event in service.stream_run(prepared)]

    async with mysql_database.session_factory() as session:
        tool_call = await session.scalar(
            select(ToolCall).where(ToolCall.run_id == prepared.run_id)
        )
    assert tool_call is not None
    assert tool_call.call_id == "call-1"
    assert tool_call.status == "completed"
    assert not tool_call.is_error
    assert tool_call.result_json == {
        "content": "读取完成",
        "is_error": False,
    }


async def test_mysql_failed_run_releases_claim(mysql_database):
    service = _service(mysql_database, FailingRunner())
    conversation = await service.create_conversation("失败测试")
    prepared = await service.prepare_run(conversation.id, "触发失败")

    events = [event async for event in service.stream_run(prepared)]

    run = await service.get_run(prepared.run_id)
    saved_conversation = await service.get_conversation(conversation.id)
    assert [event.type for event in events] == ["run.failed"]
    assert run.status == "failed"
    assert run.error_code == "AgentRuntimeError"
    assert saved_conversation.active_run_id is None


async def test_mysql_atomic_claim_allows_only_one_active_run(mysql_database):
    service = _service(mysql_database, CompletingRunner())
    conversation = await service.create_conversation("并发竞争")

    results = await asyncio.gather(
        service.prepare_run(conversation.id, "请求一"),
        service.prepare_run(conversation.id, "请求二"),
        return_exceptions=True,
    )

    winners = [result for result in results if isinstance(result, PreparedRun)]
    conflicts = [
        result for result in results if isinstance(result, ConversationBusyError)
    ]
    assert len(winners) == 1
    assert len(conflicts) == 1

    async with mysql_database.session_factory() as session:
        runs = list(
            (
                await session.scalars(
                    select(AgentRun).where(AgentRun.conversation_id == conversation.id)
                )
            ).all()
        )
        saved = await session.get(Conversation, conversation.id)
    assert len(runs) == 1
    assert saved is not None
    assert saved.active_run_id == winners[0].run_id


async def test_mysql_gemini_wait_does_not_hold_database_transaction(mysql_database):
    runner = PausedRunner()
    service = _service(mysql_database, runner)
    conversation = await service.create_conversation("等待模型")
    prepared = await service.prepare_run(conversation.id, "等待")

    consume_task = asyncio.create_task(_consume(service.stream_run(prepared)))
    await asyncio.wait_for(runner.started.wait(), timeout=2)

    async with mysql_database.session_factory.begin() as session:
        await asyncio.wait_for(
            session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(title="模型等待期间仍可写")
            ),
            timeout=2,
        )

    runner.release.set()
    await asyncio.wait_for(consume_task, timeout=2)


async def test_mysql_closing_stream_marks_run_cancelled(mysql_database):
    service = _service(mysql_database, HangingRunner())
    conversation = await service.create_conversation("取消测试")
    prepared = await service.prepare_run(conversation.id, "生成长回答")
    stream = service.stream_run(prepared)

    first = await anext(stream)
    assert first.type == "tool.requested"
    await stream.aclose()

    run = await service.get_run(prepared.run_id)
    saved_conversation = await service.get_conversation(conversation.id)
    async with mysql_database.session_factory() as session:
        tool_call = await session.scalar(
            select(ToolCall).where(ToolCall.run_id == prepared.run_id)
        )
    assert run.status == "cancelled"
    assert saved_conversation.active_run_id is None
    assert tool_call is not None
    assert tool_call.status == "cancelled"


async def test_mysql_runner_timeout_marks_run_failed(mysql_database):
    runner = PausedRunner()
    service = _service(mysql_database, runner, timeout=0.05)
    conversation = await service.create_conversation("超时测试")
    prepared = await service.prepare_run(conversation.id, "一直等待")

    events = [event async for event in service.stream_run(prepared)]

    run = await service.get_run(prepared.run_id)
    saved_conversation = await service.get_conversation(conversation.id)
    assert [event.type for event in events] == ["run.failed"]
    assert run.status == "failed"
    assert "超过" in (run.error_message or "")
    assert saved_conversation.active_run_id is None


async def test_mysql_slow_sse_consumer_does_not_consume_runner_timeout(
    mysql_database,
):
    service = _service(mysql_database, SlowAfterDeltaRunner(), timeout=0.08)
    conversation = await service.create_conversation("慢客户端")
    prepared = await service.prepare_run(conversation.id, "流式回答")
    stream = service.stream_run(prepared)

    first = await anext(stream)
    await asyncio.sleep(0.12)
    remaining = [event async for event in stream]

    run = await service.get_run(prepared.run_id)
    assert first.type == "text.delta"
    assert [event.type for event in remaining] == ["run.completed"]
    assert run.status == "completed"


async def test_mysql_expired_run_is_recovered_on_next_claim(mysql_database):
    service = _service(mysql_database, CompletingRunner(), timeout=0.05)
    conversation = await service.create_conversation("过期租约")
    expired = await service.prepare_run(conversation.id, "旧请求")

    old_started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
    async with mysql_database.session_factory.begin() as session:
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == expired.run_id)
            .values(started_at=old_started_at)
        )

    replacement = await service.prepare_run(conversation.id, "新请求")

    expired_run = await service.get_run(expired.run_id)
    saved_conversation = await service.get_conversation(conversation.id)
    assert expired_run.status == "failed"
    assert expired_run.error_code == "RunExpired"
    assert saved_conversation.active_run_id == replacement.run_id


async def test_mysql_http_request_gets_409_while_first_stream_is_active(
    mysql_database,
):
    runner = PausedRunner()
    service = _service(mysql_database, runner)
    conversation = await service.create_conversation("HTTP 并发")
    app = create_app(service=service)
    app.state.agent_api_service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        first_request = asyncio.create_task(
            client.post(
                f"/api/v1/conversations/{conversation.id}/runs",
                json={"message": "第一个请求"},
            )
        )
        await asyncio.wait_for(runner.started.wait(), timeout=2)

        second_response = await client.post(
            f"/api/v1/conversations/{conversation.id}/runs",
            json={"message": "第二个请求"},
        )
        assert second_response.status_code == 409

        runner.release.set()
        first_response = await asyncio.wait_for(first_request, timeout=2)
        assert first_response.status_code == 200
        assert "event: run.completed" in first_response.text


async def test_mysql_response_start_failure_cancels_prepared_run(mysql_database):
    service = _service(mysql_database, CompletingRunner())
    conversation = await service.create_conversation("首帧前断开")
    prepared = await service.prepare_run(conversation.id, "不会开始生成")
    entered = False

    async def content():
        nonlocal entered
        entered = True
        yield b"data: never\n\n"

    async def receive() -> ASGIMessage:
        return {"type": "http.disconnect"}

    async def failing_send(message: ASGIMessage) -> None:
        raise RuntimeError("响应头发送失败")

    response = ManagedStreamingResponse(
        content(),
        on_incomplete=lambda: service.cancel_run(prepared),
        media_type="text/event-stream",
    )
    scope = cast(
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

    with pytest.raises(RuntimeError, match="响应头发送失败"):
        await response(
            scope,
            cast(Receive, receive),
            cast(Send, failing_send),
        )

    run = await service.get_run(prepared.run_id)
    saved_conversation = await service.get_conversation(conversation.id)
    assert not entered
    assert run.status == "cancelled"
    assert saved_conversation.active_run_id is None


async def test_mysql_approval_wait_uses_short_transactions_and_resumes(
    mysql_database,
):
    runner = ApprovalRunner()
    service = _service(mysql_database, runner)
    conversation = await service.create_conversation("审批通过")
    prepared = await service.prepare_run(conversation.id, "修改文件")
    stream = service.stream_run(prepared)

    requested = await anext(stream)
    approval_required = await anext(stream)
    waiting_run = await service.get_run(prepared.run_id)
    assert requested.type == "tool.requested"
    assert approval_required.type == "tool.approval_required"
    assert waiting_run.status == "waiting_approval"

    async with mysql_database.session_factory.begin() as session:
        await asyncio.wait_for(
            session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(title="审批期间数据库可写")
            ),
            timeout=2,
        )

    with pytest.raises(ConversationBusyError):
        await service.prepare_run(conversation.id, "并发请求")

    decision = await service.decide_tool_call(
        prepared.run_id,
        "approval-call",
        "approve",
        "本地确认",
    )
    remaining = [event async for event in stream]

    assert decision.approval_status == "approved"
    assert runner.executed
    assert [event.type for event in remaining] == [
        "tool.completed",
        "run.completed",
    ]
    async with mysql_database.session_factory() as session:
        tool_call = await session.scalar(
            select(ToolCall).where(ToolCall.run_id == prepared.run_id)
        )
    assert tool_call is not None
    assert tool_call.status == "completed"
    assert tool_call.approval_status == "approved"
    assert tool_call.approval_decided_by == "local_operator"
    assert tool_call.approval_reason == "本地确认"


async def test_mysql_rejected_approval_is_audited_and_not_executed(mysql_database):
    runner = ApprovalRunner()
    service = _service(mysql_database, runner)
    conversation = await service.create_conversation("审批拒绝")
    prepared = await service.prepare_run(conversation.id, "修改文件")
    stream = service.stream_run(prepared)
    await anext(stream)
    await anext(stream)

    await service.decide_tool_call(
        prepared.run_id,
        "approval-call",
        "reject",
        "不允许修改",
    )
    events = [event async for event in stream]

    assert not runner.executed
    assert events[0].type == "tool.completed"
    assert events[0].data["is_error"] is True
    async with mysql_database.session_factory() as session:
        tool_call = await session.scalar(
            select(ToolCall).where(ToolCall.run_id == prepared.run_id)
        )
    assert tool_call is not None
    assert tool_call.status == "rejected"
    assert tool_call.approval_status == "rejected"
    assert tool_call.approval_decided_by == "local_operator"


async def test_mysql_approval_timeout_auto_rejects(mysql_database):
    runner = ApprovalRunner()
    service = _service(
        mysql_database,
        runner,
        timeout=1,
        approval_timeout=0.03,
    )
    conversation = await service.create_conversation("审批超时")
    prepared = await service.prepare_run(conversation.id, "修改文件")

    events = [event async for event in service.stream_run(prepared)]

    assert [event.type for event in events] == [
        "tool.requested",
        "tool.approval_required",
        "tool.completed",
        "run.completed",
    ]
    assert not runner.executed
    async with mysql_database.session_factory() as session:
        tool_call = await session.scalar(
            select(ToolCall).where(ToolCall.run_id == prepared.run_id)
        )
    assert tool_call is not None
    assert tool_call.status == "rejected"
    assert tool_call.approval_status == "expired"
    assert tool_call.approval_decided_by == "system_timeout"


async def test_mysql_concurrent_approval_decisions_have_one_winner(mysql_database):
    runner = ApprovalRunner()
    service = _service(mysql_database, runner)
    conversation = await service.create_conversation("审批竞争")
    prepared = await service.prepare_run(conversation.id, "修改文件")
    stream = service.stream_run(prepared)
    await anext(stream)
    await anext(stream)

    results = await asyncio.gather(
        service.decide_tool_call(
            prepared.run_id,
            "approval-call",
            "approve",
            "请求一",
        ),
        service.decide_tool_call(
            prepared.run_id,
            "approval-call",
            "reject",
            "请求二",
        ),
        return_exceptions=True,
    )
    _ = [event async for event in stream]

    winners = [result for result in results if isinstance(result, ToolCall)]
    conflicts = [
        result for result in results if isinstance(result, ApprovalConflictError)
    ]
    assert len(winners) == 1
    assert len(conflicts) == 1


async def test_mysql_disconnect_cancels_pending_approval(mysql_database):
    service = _service(mysql_database, ApprovalRunner())
    conversation = await service.create_conversation("审批中断开")
    prepared = await service.prepare_run(conversation.id, "修改文件")
    stream = service.stream_run(prepared)
    await anext(stream)
    await anext(stream)

    await stream.aclose()

    run = await service.get_run(prepared.run_id)
    async with mysql_database.session_factory() as session:
        tool_call = await session.scalar(
            select(ToolCall).where(ToolCall.run_id == prepared.run_id)
        )
    assert run.status == "cancelled"
    assert tool_call is not None
    assert tool_call.status == "cancelled"
    assert tool_call.approval_status == "cancelled"
    assert tool_call.approval_decided_by == "client_disconnect"


async def _consume(stream: AsyncGenerator) -> list:
    return [event async for event in stream]
