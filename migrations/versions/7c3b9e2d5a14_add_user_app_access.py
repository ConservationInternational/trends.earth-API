"""Add per-application access grants and gated OAuth clients.

Revision ID: 7c3b9e2d5a14
Revises: 9d4e6f1a2b3c
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "7c3b9e2d5a14"
down_revision = "9d4e6f1a2b3c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_app_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_key", sa.String(length=50), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "role", sa.String(length=20), nullable=False, server_default="member"
        ),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("request_note", sa.Text(), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "granted_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"], ["user.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "app_key", name="uq_user_app_access_user_app"),
    )
    op.create_index("ix_user_app_access_user_id", "user_app_access", ["user_id"])
    op.create_index(
        "ix_user_app_access_app_key_status", "user_app_access", ["app_key", "status"]
    )

    op.add_column(
        "oauth_clients",
        sa.Column("required_app_key", sa.String(length=50), nullable=True),
    )

    # Fail loudly rather than silently dropping rows if audiences collide.
    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text(
            "SELECT audience, count(*) FROM oauth_clients "
            "GROUP BY audience HAVING count(*) > 1"
        )
    ).fetchall()
    if duplicates:
        collisions = ", ".join(f"{row[0]} (x{row[1]})" for row in duplicates)
        raise RuntimeError(
            "Cannot add a UNIQUE constraint on oauth_clients.audience: duplicate "
            f"audiences exist and must be resolved manually first: {collisions}"
        )
    op.create_unique_constraint(
        "uq_oauth_clients_audience", "oauth_clients", ["audience"]
    )


def downgrade():
    op.drop_constraint("uq_oauth_clients_audience", "oauth_clients", type_="unique")
    op.drop_column("oauth_clients", "required_app_key")
    op.drop_index("ix_user_app_access_app_key_status", table_name="user_app_access")
    op.drop_index("ix_user_app_access_user_id", table_name="user_app_access")
    op.drop_table("user_app_access")
