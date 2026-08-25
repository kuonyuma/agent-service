from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateConversationRequest(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=200)

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={"examples": [{"title": "分析这个 Python 项目"}]},
    )


class ConversationResponse(ORMResponse):
    id: str
    title: str
    active_run_id: str | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(ORMResponse):
    id: str
    conversation_id: str
    run_id: str | None
    sequence_no: int
    role: str
    kind: str
    content: str
    created_at: datetime


class CreateRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={"examples": [{"message": "列出当前目录中的 Python 文件"}]},
    )


class RunResponse(ORMResponse):
    id: str
    conversation_id: str
    status: Literal[
        "running",
        "waiting_approval",
        "completed",
        "failed",
        "cancelled",
    ]
    turn_count: int
    input_tokens: int
    output_tokens: int
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class ToolCallResponse(ORMResponse):
    id: str
    run_id: str
    call_id: str
    tool_name: str
    arguments_json: dict[str, Any]
    result_json: dict[str, Any] | None
    result_content: str | None
    status: Literal[
        "requested",
        "waiting_approval",
        "completed",
        "failed",
        "rejected",
        "cancelled",
    ]
    is_error: bool
    requires_approval: bool
    approval_status: Literal[
        "pending",
        "approved",
        "rejected",
        "expired",
        "cancelled",
    ] | None
    approval_requested_at: datetime | None
    approval_expires_at: datetime | None
    approval_decided_at: datetime | None
    approval_decided_by: str | None
    approval_reason: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2_000)

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "decision": "approve",
                    "reason": "允许本次本地文件修改",
                }
            ]
        },
    )


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
