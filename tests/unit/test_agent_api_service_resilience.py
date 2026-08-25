import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from agent_service.core.agentic_loop import LoopEvent
from agent_service.core.runner import AgentRunner
from agent_service.db import Database
from agent_service.services.agent_api_service import AgentAPIService, PreparedRun
from agent_service.services.events import RunStreamEvent


class ToolRequestRunner:
    async def run_turn(
        self,
        history,
        user_query,
        permission_check=None,
    ) -> AsyncGenerator[LoopEvent]:
        yield LoopEvent(
            type="tool.requested",
            turn=1,
            tool_call_id="call-db-error",
            tool_name="read_file",
            tool_arguments={"path": "README.md"},
        )


@pytest.mark.asyncio
async def test_stream_always_finishes_when_failure_persistence_also_fails(
    monkeypatch,
):
    database = cast(Database, SimpleNamespace(session_factory=None))
    service = AgentAPIService(
        database,
        cast(AgentRunner, ToolRequestRunner()),
    )
    monkeypatch.setattr(
        service,
        "_save_tool_requested",
        AsyncMock(side_effect=RuntimeError("数据库写入失败")),
    )
    monkeypatch.setattr(
        service,
        "_safe_mark_failed",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        service,
        "_cancel_with_shield",
        AsyncMock(side_effect=RuntimeError("取消状态也无法写入")),
    )
    queue: asyncio.Queue[RunStreamEvent | None] = asyncio.Queue(maxsize=8)
    prepared = PreparedRun(
        run_id="run-1",
        conversation_id="conversation-1",
        message="触发错误",
        history=[],
        request_id="request-1",
    )

    await service._produce_run(prepared, queue)

    failed = queue.get_nowait()
    sentinel = queue.get_nowait()
    assert failed is not None
    assert failed.type == "run.failed"
    assert "数据库写入失败" in failed.data["error"]
    assert sentinel is None


def test_closing_full_event_queue_makes_room_for_sentinel():
    queue: asyncio.Queue[RunStreamEvent | None] = asyncio.Queue(maxsize=1)
    queue.put_nowait(
        RunStreamEvent(
            type="text.delta",
            sequence=1,
            data={"text": "可丢弃的旧增量"},
        )
    )

    AgentAPIService._close_event_queue(queue)

    assert queue.get_nowait() is None
