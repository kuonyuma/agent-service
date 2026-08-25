from datetime import UTC, datetime
from typing import Any
from uuid import uuid7

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from agent_service.db.base import Base

RUN_STATUSES = (
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
)
TOOL_CALL_STATUSES = (
    "requested",
    "waiting_approval",
    "completed",
    "failed",
    "rejected",
    "cancelled",
)
APPROVAL_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "expired",
    "cancelled",
)


def _new_uuid() -> str:
    return str(uuid7())


def _utc_now() -> datetime:
    # MySQL DATETIME 不保存时区；统一写入无时区标记的 UTC 值。
    return datetime.now(UTC).replace(tzinfo=None)


class UUIDPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6),
        default=_utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
    )


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "next_message_seq >= 1",
            name="next_message_seq_positive",
        ),
        Index("ix_conversations_active_run_id", "active_run_id"),
        Index("ix_conversations_created_at", "created_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    title: Mapped[str] = mapped_column(
        String(200),
        default="新会话",
        server_default=text("'新会话'"),
    )
    # 不设置外键，避免 conversations 与 agent_runs 形成循环依赖。
    active_run_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    next_message_seq: Mapped[int] = mapped_column(
        BigInteger,
        default=1,
        server_default=text("1"),
    )


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'waiting_approval', 'completed', "
            "'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("turn_count >= 0", name="turn_count_nonnegative"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0",
            name="tokens_nonnegative",
        ),
        Index("ix_agent_runs_conversation_status", "conversation_id", "status"),
        Index("ix_agent_runs_created_at", "created_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    conversation_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="running",
        server_default=text("'running'"),
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(mysql.LONGTEXT, nullable=True)
    turn_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
    )
    input_tokens: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default=text("0"),
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default=text("0"),
    )
    started_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6),
        default=_utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )


class Message(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_messages_conversation_sequence",
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_no_positive"),
        Index("ix_messages_run_id", "run_id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    conversation_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
    )
    run_id: Mapped[str | None] = mapped_column(
        CHAR(36),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(mysql.LONGTEXT)
    payload_json: Mapped[dict[str, Any]] = mapped_column(mysql.JSON)
    created_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6),
        default=_utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
    )


class ToolCall(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "call_id",
            name="uq_tool_calls_run_call",
        ),
        CheckConstraint(
            "status IN ('requested', 'waiting_approval', 'completed', "
            "'failed', 'rejected', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "approval_status IS NULL OR approval_status IN "
            "('pending', 'approved', 'rejected', 'expired', 'cancelled')",
            name="approval_status",
        ),
        Index("ix_tool_calls_run_status", "run_id", "status"),
        Index(
            "ix_tool_calls_run_approval_status",
            "run_id",
            "approval_status",
        ),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    run_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
    )
    call_id: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments_json: Mapped[dict[str, Any]] = mapped_column(mysql.JSON)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        mysql.JSON,
        nullable=True,
    )
    result_content: Mapped[str | None] = mapped_column(mysql.LONGTEXT, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default="requested",
        server_default=text("'requested'"),
    )
    is_error: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
    )
    approval_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    approval_request_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    approval_requested_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )
    approval_expires_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )
    approval_decided_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )
    approval_decided_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    approval_reason: Mapped[str | None] = mapped_column(
        mysql.LONGTEXT,
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6),
        nullable=True,
    )
