"""真实 MySQL 上验证安全中止的审批、会话租约和历史处理，不调用 Gemini。"""

import asyncio
import json
from typing import cast

import pytest

from agent_service.config.settings import SafetyConfig
from agent_service.core.agentic_loop import LoopEvent, ToolApprovalRequest
from agent_service.core.coordinator import RunCoordinator
from agent_service.core.runner import AgentRunner
from agent_service.errors import ApprovalConflictError
from agent_service.safety.reviewer import ReviewResult
from agent_service.services.agent_api_service import AgentAPIService


@pytest.mark.parametrize("verdict", ["block", "error"])
async def test_mysql_safety_cancels_approval_releases_lease_and_sanitizes_history(
    mysql_database, verdict
):
    waiting = asyncio.Event()
    executed = False
    raw_text = "审核拦截测试原文"

    class Reviewer:
        async def check(self, text):
            await waiting.wait()
            return ReviewResult(verdict, "violence" if verdict == "block" else "none")

    class Runner:
        async def run_turn(self, permission_check, run_control, **kwargs):
            nonlocal executed
            yield LoopEvent(type="text.delta", text=raw_text)
            request = ToolApprovalRequest.create(
                "call-safety", "write_file", {"path": "unused.txt", "content": "测试"}
            )
            yield LoopEvent(
                type="tool.requested",
                tool_call_id=request.call_id,
                tool_name=request.tool_name,
                tool_arguments=request.arguments,
                requires_approval=True,
                approval_request_hash=request.request_hash,
            )
            if await permission_check(request):
                run_control.ensure_running()
                executed = True

    service = AgentAPIService(
        mysql_database,
        RunCoordinator(
            cast(AgentRunner, Runner()), Reviewer(), SafetyConfig(chunk_chars=1)
        ),
    )
    original_wait = service._wait_for_approval

    async def mark_waiting(prepared, request):
        waiting.set()
        return await original_wait(prepared, request)

    service._wait_for_approval = mark_waiting
    conversation = await service.create_conversation("安全审核测试")
    prepared = await service.prepare_run(conversation.id, "请执行测试")
    async with asyncio.timeout(10):
        events = [event async for event in service.stream_run(prepared)]
    assert [event.type for event in events][-2:] == ["safety.blocked", "run.failed"]
    run = await service.get_run(prepared.run_id)
    assert run.status == "failed"
    assert run.error_code == (
        "SafetyBlockedError" if verdict == "block" else "SafetyReviewError"
    )
    assert (await service.get_conversation(conversation.id)).active_run_id is None
    (call,) = await service.list_tool_calls(prepared.run_id)
    assert call.status == "cancelled" and call.approval_status == "cancelled"
    assert call.result_content is None and call.result_json is None
    messages = await service.list_messages(conversation.id)
    assert raw_text not in "".join(message.content for message in messages)
    assert raw_text not in json.dumps(
        [message.payload_json for message in messages], ensure_ascii=False
    )
    assert not executed and not service._approval_waiters
    with pytest.raises(ApprovalConflictError):
        await service.decide_tool_call(prepared.run_id, call.call_id, "approve", None)
    next_run = await service.prepare_run(conversation.id, "继续")
    assert raw_text not in str(next_run.history)
    await service.cancel_run(next_run)
