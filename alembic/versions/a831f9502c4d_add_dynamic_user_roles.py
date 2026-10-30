"""add dynamic roles and user-role assignments

Revision ID: a831f9502c4d
Revises: 9f3a2c7d1b40
Create Date: 2026-10-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a831f9502c4d"
down_revision: Union[str, Sequence[str], None] = "9f3a2c7d1b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BUILT_IN_ROLES = ("admin", "manager", "user", "viewer")


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    connection = op.get_bind()
    roles_table = sa.table(
        "roles",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    users_table = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("role", sa.String()),
    )
    assignments_table = sa.table(
        "user_roles",
        sa.column("user_id", sa.Integer()),
        sa.column("role_id", sa.Integer()),
    )

    names = list(BUILT_IN_ROLES)
    names.extend(
        name
        for (name,) in connection.execute(
            sa.select(users_table.c.role).distinct()
        )
        if name not in names
    )
    role_ids: dict[str, int] = {}
    for name in names:
        result = connection.execute(
            roles_table.insert()
            .values(name=name)
        )
        role_ids[name] = connection.scalar(
            sa.select(roles_table.c.id).where(roles_table.c.name == name)
        )

    for user_id, role_name in connection.execute(
        sa.select(users_table.c.id, users_table.c.role)
    ):
        connection.execute(
            assignments_table.insert().values(
                user_id=user_id,
                role_id=role_ids[role_name],
            )
        )


def downgrade() -> None:
    op.drop_table("user_roles")
    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_table("roles")
