"""Allow the QGIS plugin and Trends.Earth web client to share an API audience."""

from alembic import op

revision = "c4e91b7a2d6f"
down_revision = "7c3b9e2d5a14"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_oauth_clients_audience", "oauth_clients", type_="unique")
    op.execute(
        "UPDATE oauth_clients "
        "SET audience = 'https://api.trends.earth' "
        "WHERE client_id = 'te-qgis-plugin'"
    )


def downgrade():
    op.execute(
        "UPDATE oauth_clients SET audience = 'trends-earth-qgis' "
        "WHERE client_id = 'te-qgis-plugin'"
    )
    op.execute(
        "UPDATE oauth_clients SET audience = 'trends-earth-web' "
        "WHERE client_id = 'te-web'"
    )
    op.create_unique_constraint(
        "uq_oauth_clients_audience", "oauth_clients", ["audience"]
    )
