"""Add gee_cloud_project column to users table.

The EE Python SDK derives the GCP project from the OAuth client ID when no
project is supplied to ee.Initialize().  For the server's OAuth client this
always resolves to the server's project (gef-ld-toolbox), which ordinary users
don't have serviceusage.serviceUsageConsumer on.  Storing the user's own GCP
project ID here lets us pass project= explicitly so EE uses the correct
billing project.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("gee_cloud_project", sa.String(length=100), nullable=True),
    )


def downgrade():
    op.drop_column("users", "gee_cloud_project")
