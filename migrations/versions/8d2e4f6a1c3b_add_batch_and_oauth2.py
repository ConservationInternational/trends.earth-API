"""Add batch columns to script and service_client table for OAuth2

Adds compute_type, batch_job_definition, batch_job_queue to the script
table, and creates the service_client table used by OAuth2 Client
Credentials authentication.

Revision ID: 8d2e4f6a1c3b
Revises: 2c4f8e1a9b3d
Create Date: 2026-02-28 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8d2e4f6a1c3b"
down_revision = "2c4f8e1a9b3d"
branch_labels = None
depends_on = None


def upgrade():
    # -- Script batch columns -------------------------------------------------
    op.add_column(
        "script",
        sa.Column(
            "compute_type",
            sa.String(length=40),
            nullable=False,
            server_default="docker",
        ),
    )
    op.add_column(
        "script",
        sa.Column("batch_job_definition", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "script",
        sa.Column("batch_job_queue", sa.String(length=255), nullable=True),
    )

    # -- OAuth2 service_client table ------------------------------------------
    op.create_table(
        "service_client",
        sa.Column("id", sa.CHAR(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("client_secret_hash", sa.String(64), nullable=False),
        sa.Column("secret_prefix", sa.String(16), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "user_id", sa.CHAR(32), sa.ForeignKey("user.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "revoked", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_service_client_client_id",
        "service_client",
        ["client_id"],
        unique=True,
    )
    op.create_index(
        "ix_service_client_user_id", "service_client", ["user_id"]
    )


def downgrade():
    # -- OAuth2 service_client table ------------------------------------------
    op.drop_index("ix_service_client_user_id", table_name="service_client")
    op.drop_index("ix_service_client_client_id", table_name="service_client")
    op.drop_table("service_client")

    # -- Script batch columns -------------------------------------------------
    op.drop_column("script", "batch_job_queue")
    op.drop_column("script", "batch_job_definition")
    op.drop_column("script", "compute_type")
