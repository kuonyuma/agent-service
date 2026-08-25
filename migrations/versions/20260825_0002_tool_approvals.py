"""增加工具审批审计字段。

Revision ID: 20260825_0002
Revises: 20260825_0001
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260825_0002"
down_revision: str | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 tool_calls 增加一次性审批状态与审计信息。"""

    op.add_column(
        "tool_calls",
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_request_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_requested_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_expires_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_decided_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_decided_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_reason", mysql.LONGTEXT(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_tool_calls_approval_status"),
        "tool_calls",
        "approval_status IS NULL OR approval_status IN "
        "('pending', 'approved', 'rejected', 'expired', 'cancelled')",
    )
    op.create_index(
        "ix_tool_calls_run_approval_status",
        "tool_calls",
        ["run_id", "approval_status"],
        unique=False,
    )


def downgrade() -> None:
    """删除工具审批字段。"""

    op.drop_index(
        "ix_tool_calls_run_approval_status",
        table_name="tool_calls",
    )
    op.drop_constraint(
        op.f("ck_tool_calls_approval_status"),
        "tool_calls",
        type_="check",
    )
    op.drop_column("tool_calls", "approval_reason")
    op.drop_column("tool_calls", "approval_decided_by")
    op.drop_column("tool_calls", "approval_decided_at")
    op.drop_column("tool_calls", "approval_expires_at")
    op.drop_column("tool_calls", "approval_requested_at")
    op.drop_column("tool_calls", "approval_request_hash")
    op.drop_column("tool_calls", "approval_status")
    op.drop_column("tool_calls", "requires_approval")
