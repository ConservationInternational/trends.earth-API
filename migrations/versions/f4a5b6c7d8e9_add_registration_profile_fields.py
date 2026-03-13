"""Add registration profile fields

Revision ID: f4a5b6c7d8e9
Revises: c4d5e6f7a8b9
Create Date: 2026-03-10 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f4a5b6c7d8e9"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("role_title", sa.String(length=200), nullable=True))
    op.add_column("user", sa.Column("sector", sa.String(length=120), nullable=True))
    op.add_column("user", sa.Column("sector_other", sa.String(length=200), nullable=True))
    op.add_column(
        "user", sa.Column("gender_identity", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "user",
        sa.Column("gender_identity_description", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "user", sa.Column("gee_license_acknowledged", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "user", sa.Column("purpose_of_use", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "user", sa.Column("purpose_of_use_other", sa.String(length=200), nullable=True)
    )

    # Add indices for commonly filtered/sorted fields
    op.create_index("ix_user_sector", "user", ["sector"], unique=False)
    op.create_index("ix_user_purpose_of_use", "user", ["purpose_of_use"], unique=False)
    op.create_index(
        "ix_user_gee_license_acknowledged",
        "user",
        ["gee_license_acknowledged"],
        unique=False,
    )


def downgrade():
    # Drop indices first
    op.drop_index("ix_user_gee_license_acknowledged", table_name="user")
    op.drop_index("ix_user_purpose_of_use", table_name="user")
    op.drop_index("ix_user_sector", table_name="user")

    op.drop_column("user", "purpose_of_use_other")
    op.drop_column("user", "purpose_of_use")
    op.drop_column("user", "gee_license_acknowledged")
    op.drop_column("user", "gender_identity_description")
    op.drop_column("user", "gender_identity")
    op.drop_column("user", "sector_other")
    op.drop_column("user", "sector")
    op.drop_column("user", "role_title")
