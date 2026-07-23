"""add_claim_worker_fields

Revision ID: 8d26f149b2c1
Revises: 1e0b298ed0ac
Create Date: 2026-07-23 22:55:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d26f149b2c1"
down_revision: Union[str, Sequence[str], None] = "1e0b298ed0ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("condition_id", sa.String(length=66), nullable=True))
    op.add_column("trades", sa.Column("token_id", sa.String(length=128), nullable=True))
    op.add_column(
        "trades", sa.Column("claim_transaction_id", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "trades", sa.Column("claim_transaction_hash", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "trades",
        sa.Column("claim_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("trades", sa.Column("claim_error", sa.Text(), nullable=True))
    op.add_column(
        "trades", sa.Column("claim_next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "trades", sa.Column("claim_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "trades", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(op.f("ix_trades_condition_id"), "trades", ["condition_id"], unique=False)
    op.create_index(
        "ix_trades_claim_queue",
        "trades",
        ["claim_status", "claim_next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_trades_claim_queue", table_name="trades")
    op.drop_index(op.f("ix_trades_condition_id"), table_name="trades")
    op.drop_column("trades", "claimed_at")
    op.drop_column("trades", "claim_updated_at")
    op.drop_column("trades", "claim_next_attempt_at")
    op.drop_column("trades", "claim_error")
    op.drop_column("trades", "claim_attempts")
    op.drop_column("trades", "claim_transaction_hash")
    op.drop_column("trades", "claim_transaction_id")
    op.drop_column("trades", "token_id")
    op.drop_column("trades", "condition_id")
