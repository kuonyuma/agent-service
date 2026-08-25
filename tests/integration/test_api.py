from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from agent_service.api.app import create_app
from agent_service.errors import (
    ApprovalConflictError,
    ConversationBusyError,
    ConversationNotFoundError,
)
from agent_service.services.agent_api_service import AgentAPIService, PreparedRun
from agent_service.services.events import RunStreamEvent


class FakeAgentAPIService:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)
        self.ready_calls = 0
        self.actions: list[str] = []
        self.ready_value = True
        self.conflict = False
        self.not_found = False
        self.database_error = False
        self.approval_conflict = False

    async def ready(self) -> bool:
        self.ready_calls += 1
        return self.ready_value

    async def create_conversation(self, title: str):
        return self._conversation(title)

    async def get_conversation(self, conversation_id: str):
        if self.database_error:
            raise SQLAlchemyError("数据库断开")
        if self.not_found:
            raise ConversationNotFoundError(f"会话不存在：{conversation_id}")
        return self._conversation("测试会话", conversation_id)

    async def list_messages(self, conversation_id: str):
        if self.not_found:
            raise ConversationNotFoundError(f"会话不存在：{conversation_id}")
        return [
            SimpleNamespace(
                id="00000000-0000-7000-8000-000000000002",
                conversation_id=conversation_id,
                run_id="00000000-0000-7000-8000-000000000003",
                sequence_no=1,
                role="user",
                kind="user_message",
                content="你好",
                created_at=self.now,
            )
        ]

    async def prepare_run(
        self,
        conversation_id: str,
        message: str,
        *,
        request_id: str = "",
    ) -> PreparedRun:
        self.actions.append("prepare")
        if self.conflict:
            raise ConversationBusyError("会话已有活跃运行")
        return PreparedRun(
            run_id="00000000-0000-7000-8000-000000000003",
            conversation_id=conversation_id,
            message=message,
            history=[],
            request_id=request_id,
        )

    async def stream_run(
        self,
        prepared: PreparedRun,
    ) -> AsyncGenerator[RunStreamEvent]:
        self.actions.append("stream")
        yield RunStreamEvent(
            type="text.delta",
            sequence=1,
            data={"run_id": prepared.run_id, "turn": 1, "text": "你好"},
        )
        yield RunStreamEvent(
            type="run.completed",
            sequence=2,
            data={"run_id": prepared.run_id, "status": "completed"},
        )

    async def get_run(self, run_id: str):
        return SimpleNamespace(
            id=run_id,
            conversation_id="00000000-0000-7000-8000-000000000001",
            status="completed",
            turn_count=1,
            input_tokens=4,
            output_tokens=2,
            error_code=None,
            error_message=None,
            started_at=self.now,
            finished_at=self.now,
        )

    async def list_tool_calls(self, run_id: str):
        return [self._tool_call(run_id, approval_status="pending")]

    async def decide_tool_call(
        self,
        run_id: str,
        call_id: str,
        decision: str,
        reason: str | None,
    ):
        self.actions.append(f"approval:{decision}:{reason}")
        if self.approval_conflict:
            raise ApprovalConflictError("审批已经终结")
        return self._tool_call(
            run_id,
            call_id=call_id,
            approval_status="approved" if decision == "approve" else "rejected",
        )

    def _tool_call(
        self,
        run_id: str,
        *,
        call_id: str = "call-1",
        approval_status: str,
    ):
        return SimpleNamespace(
            id="00000000-0000-7000-8000-000000000004",
            run_id=run_id,
            call_id=call_id,
            tool_name="write_file",
            arguments_json={"path": "notes.txt", "content": "内容"},
            result_json=None,
            result_content=None,
            status=(
                "waiting_approval"
                if approval_status == "pending"
                else "requested"
            ),
            is_error=False,
            requires_approval=True,
            approval_status=approval_status,
            approval_requested_at=self.now,
            approval_expires_at=self.now,
            approval_decided_at=(
                None if approval_status == "pending" else self.now
            ),
            approval_decided_by=(
                None if approval_status == "pending" else "local_operator"
            ),
            approval_reason=None,
            created_at=self.now,
            updated_at=self.now,
            completed_at=None,
        )

    def _conversation(
        self,
        title: str,
        conversation_id: str = "00000000-0000-7000-8000-000000000001",
    ):
        return SimpleNamespace(
            id=conversation_id,
            title=title,
            active_run_id=None,
            created_at=self.now,
            updated_at=self.now,
        )


def make_client(fake: FakeAgentAPIService) -> TestClient:
    return TestClient(create_app(service=cast(AgentAPIService, fake)))


def test_minimum_api_contract_and_sse_stream():
    fake = FakeAgentAPIService()
    with make_client(fake) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert fake.ready_calls == 0

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert fake.ready_calls == 1

        created = client.post(
            "/api/v1/conversations",
            json={"title": "测试会话"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 200
        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages")
        assert messages.status_code == 200
        assert messages.json()[0]["kind"] == "user_message"

        stream = client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            json={"message": "  你好  "},
        )
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["x-run-id"]
        assert "event: text.delta" in stream.text
        assert "event: run.completed" in stream.text
        assert fake.actions == ["prepare", "stream"]

        run_id = stream.headers["x-run-id"]
        run = client.get(f"/api/v1/runs/{run_id}")
        assert run.status_code == 200
        assert run.json()["status"] == "completed"


def test_api_maps_not_found_and_active_run_conflict():
    fake = FakeAgentAPIService()
    with make_client(fake) as client:
        fake.not_found = True
        missing = client.get(
            "/api/v1/conversations/00000000-0000-7000-8000-000000000099"
        )
        assert missing.status_code == 404

        fake.conflict = True
        conflict = client.post(
            "/api/v1/conversations/00000000-0000-7000-8000-000000000001/runs",
            json={"message": "再次运行"},
        )
        assert conflict.status_code == 409
        assert fake.actions == ["prepare"]


def test_api_rejects_blank_run_message_before_service_call():
    fake = FakeAgentAPIService()
    with make_client(fake) as client:
        response = client.post(
            "/api/v1/conversations/00000000-0000-7000-8000-000000000001/runs",
            json={"message": "   "},
        )
    assert response.status_code == 422
    assert fake.actions == []


def test_ready_returns_503_when_database_is_unavailable():
    fake = FakeAgentAPIService()
    fake.ready_value = False
    with make_client(fake) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_database_error_has_stable_503_mapping_and_request_id():
    fake = FakeAgentAPIService()
    fake.database_error = True
    with make_client(fake) as client:
        response = client.get(
            "/api/v1/conversations/00000000-0000-7000-8000-000000000001",
            headers={"X-Request-ID": "request-test-1"},
        )
    assert response.status_code == 503
    assert response.headers["x-request-id"] == "request-test-1"
    assert response.json()["request_id"] == "request-test-1"


def test_tool_approval_routes_and_conflict_mapping():
    fake = FakeAgentAPIService()
    run_id = "00000000-0000-7000-8000-000000000003"
    with make_client(fake) as client:
        listed = client.get(f"/api/v1/runs/{run_id}/tool-calls")
        assert listed.status_code == 200
        assert listed.json()[0]["approval_status"] == "pending"

        approved = client.post(
            f"/api/v1/runs/{run_id}/tool-calls/call-1/approval",
            json={"decision": "approve", "reason": "本地确认"},
        )
        assert approved.status_code == 200
        assert approved.json()["approval_status"] == "approved"
        assert fake.actions == ["approval:approve:本地确认"]

        fake.approval_conflict = True
        conflict = client.post(
            f"/api/v1/runs/{run_id}/tool-calls/call-1/approval",
            json={"decision": "reject"},
        )
        assert conflict.status_code == 409
