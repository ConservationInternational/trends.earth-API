"""Add gee_cloud_project_number column to users table.

The GEE service agent email for a user's GCP project is
``service-{PROJECT_NUMBER}@gcp-sa-earthengine.iam.gserviceaccount.com``.
This numeric project number is distinct from the human-readable project ID
(``gee_cloud_project``) and is required to construct the service agent email
so we can grant it ``roles/storage.objectCreator`` on the output GCS bucket.

Revision ID: a8c2e5f1b3d6
Revises: f3a4b5c6d7e8
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a8c2e5f1b3d6"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("gee_cloud_project_number", sa.BigInteger(), nullable=True),
    )


def downgrade():
    op.drop_column("user", "gee_cloud_project_number")
