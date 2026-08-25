"""创建 Agent 服务核心表。

Revision ID: 20260825_0001
Revises:
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260825_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建会话、运行、消息和工具调用表。"""

    op.create_table(
        "conversations",
        sa.Column(
            "title",
            sa.String(length=200),
            server_default=sa.text("'新会话'"),
            nullable=False,
        ),
        sa.Column("active_run_id", sa.CHAR(length=36), nullable=True),
        sa.Column(
            "next_message_seq",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "next_message_seq >= 1",
            name=op.f("ck_conversations_next_message_seq_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_conversations_active_run_id",
        "conversations",
        ["active_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_created_at",
        "conversations",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "agent_runs",
        sa.Column("conversation_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", mysql.LONGTEXT(), nullable=True),
        sa.Column(
            "turn_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "input_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "output_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'waiting_approval', 'completed', "
            "'failed', 'cancelled')",
            name=op.f("ck_agent_runs_status"),
        ),
        sa.CheckConstraint(
            "turn_count >= 0",
            name=op.f("ck_agent_runs_turn_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0",
            name=op.f("ck_agent_runs_tokens_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_agent_runs_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_agent_runs_conversation_status",
        "agent_runs",
        ["conversation_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_created_at",
        "agent_runs",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.CHAR(length=36), nullable=False),
        sa.Column("run_id", sa.CHAR(length=36), nullable=True),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", mysql.LONGTEXT(), nullable=False),
        sa.Column("payload_json", mysql.JSON(), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.CheckConstraint(
            "sequence_no >= 1",
            name=op.f("ck_messages_sequence_no_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_messages_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_messages_conversation_sequence",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_messages_run_id",
        "messages",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "tool_calls",
        sa.Column("run_id", sa.CHAR(length=36), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("arguments_json", mysql.JSON(), nullable=False),
        sa.Column("result_json", mysql.JSON(), nullable=True),
        sa.Column("result_content", mysql.LONGTEXT(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'requested'"),
            nullable=False,
        ),
        sa.Column(
            "is_error", sa.Boolean(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'waiting_approval', 'completed', "
            "'failed', 'rejected', 'cancelled')",
            name=op.f("ck_tool_calls_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_tool_calls_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        sa.UniqueConstraint(
            "run_id",
            "call_id",
            name="uq_tool_calls_run_call",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "ix_tool_calls_run_status",
        "tool_calls",
        ["run_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    """按外键依赖的逆序删除核心表。"""

    # MySQL 可能复用显式索引来支撑外键；先 drop_index 会触发 1553。
    # 删除整张表会一并删除其索引，因此这里只按外键依赖逆序删表。
    op.drop_table("tool_calls")
    op.drop_table("messages")
    op.drop_table("agent_runs")
    op.drop_table("conversations")
