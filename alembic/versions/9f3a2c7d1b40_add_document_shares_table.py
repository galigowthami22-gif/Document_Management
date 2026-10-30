"""add document shares table

Revision ID: 9f3a2c7d1b40
Revises: 6d4ec321fcf1
Create Date: 2026-10-27 16:09:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f3a2c7d1b40"
down_revision: Union[str, Sequence[str], None] = "6d4ec321fcf1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_shares",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("shared_by_user_id", sa.Integer(), nullable=False),
        sa.Column("shared_with_user_id", sa.Integer(), nullable=False),
        sa.Column("can_view", sa.Boolean(), nullable=False),
        sa.Column("can_download", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_with_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "shared_with_user_id",
            name="uq_document_recipient",
        ),
    )
    op.create_index(
        op.f("ix_document_shares_document_id"),
        "document_shares",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_shares_shared_with_user_id"),
        "document_shares",
        ["shared_with_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_document_shares_shared_with_user_id"),
        table_name="document_shares",
    )
    op.drop_index(
        op.f("ix_document_shares_document_id"),
        table_name="document_shares",
    )
    op.drop_table("document_shares")
