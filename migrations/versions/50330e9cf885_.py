"""empty message

Revision ID: 50330e9cf885
Revises: a83df9ac0d52
Create Date: 2017-05-18 08:36:07.275317

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "50330e9cf885"
down_revision = "a83df9ac0d52"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "script",
        "public",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        server_default="False",
    )
    op.add_column("user", sa.Column("country", sa.String(length=120), nullable=True))
    op.add_column(
        "user", sa.Column("institution", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "user",
        sa.Column(
            "name", sa.String(length=120), nullable=False, server_default="notset"
        ),
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("user", "name")
    op.drop_column("user", "institution")
    op.drop_column("user", "country")
    op.alter_column("script", "public", existing_type=sa.BOOLEAN(), nullable=True)
    # ### end Alembic commands ###
