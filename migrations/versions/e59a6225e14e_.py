"""empty message

Revision ID: e59a6225e14e
Revises: e79eaed8af38
Create Date: 2022-06-07 16:40:32.741819

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e59a6225e14e"
down_revision = "e79eaed8af38"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "execution",
        "user_id",
        existing_type=postgresql.UUID(),
        nullable=True,
        existing_server_default=sa.text("'8cbf56bc-39db-4c8d-b3ef-b17b23c178e9'::uuid"),
    )
    op.add_column(
        "script", sa.Column("cpu_reservation", sa.BigInteger(), nullable=True)
    )
    op.add_column("script", sa.Column("cpu_limit", sa.BigInteger(), nullable=True))
    op.add_column(
        "script", sa.Column("memory_reservation", sa.BigInteger(), nullable=True)
    )
    op.add_column("script", sa.Column("memory_limit", sa.BigInteger(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("script", "memory_limit")
    op.drop_column("script", "memory_reservation")
    op.drop_column("script", "cpu_limit")
    op.drop_column("script", "cpu_reservation")
    op.alter_column(
        "execution",
        "user_id",
        existing_type=postgresql.UUID(),
        nullable=False,
        existing_server_default=sa.text("'8cbf56bc-39db-4c8d-b3ef-b17b23c178e9'::uuid"),
    )
    # ### end Alembic commands ###
