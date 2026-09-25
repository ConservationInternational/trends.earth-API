"""Add OIDC clients, authorization codes, and account disablement."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "9d4e6f1a2b3c"
down_revision = "0fa1182925f5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("user", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_user_is_active", "user", ["is_active"])
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.add_column("refresh_tokens", sa.Column("token_hash", sa.String(64), nullable=True))
    op.alter_column("refresh_tokens", "token", nullable=True)
    op.execute(
        "UPDATE refresh_tokens SET token_hash = encode(digest(token, 'sha256'), 'hex') "
        "WHERE token IS NOT NULL"
    )
    op.execute("UPDATE refresh_tokens SET token = NULL WHERE token_hash IS NOT NULL")
    op.alter_column("refresh_tokens", "token_hash", nullable=False)
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )
    op.add_column(
        "password_reset_token", sa.Column("token_hash", sa.String(64), nullable=True)
    )
    op.alter_column("password_reset_token", "token", nullable=True)
    op.execute(
        "UPDATE password_reset_token SET token_hash = encode(digest(token, 'sha256'), 'hex') "
        "WHERE token IS NOT NULL"
    )
    op.execute(
        "UPDATE password_reset_token SET token = NULL WHERE token_hash IS NOT NULL"
    )
    op.alter_column("password_reset_token", "token_hash", nullable=False)
    op.create_index(
        "ix_password_reset_token_token_hash",
        "password_reset_token",
        ["token_hash"],
        unique=True,
    )
    op.create_table(
        "oauth_clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("client_id", sa.String(120), nullable=False, unique=True),
        sa.Column("client_secret_hash", sa.String(256), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("redirect_uris", sa.Text(), nullable=False),
        sa.Column("post_logout_redirect_uris", sa.Text(), nullable=False, server_default=""),
        sa.Column("scopes", sa.String(255), nullable=False, server_default="openid email profile"),
        sa.Column("audience", sa.String(255), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_oauth_clients_client_id", "oauth_clients", ["client_id"])
    op.create_table(
        "authorization_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(120), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("code_challenge_method", sa.String(10), nullable=False, server_default="S256"),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("nonce", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
    )
    op.create_index("ix_authorization_codes_code_hash", "authorization_codes", ["code_hash"])
    op.create_index("ix_authorization_codes_client_id", "authorization_codes", ["client_id"])
    op.create_index("ix_authorization_codes_user_id", "authorization_codes", ["user_id"])
    op.create_index("ix_authorization_codes_expires_at", "authorization_codes", ["expires_at"])
    op.create_table(
        "oidc_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
    )
    op.create_index(
        "ix_oidc_refresh_tokens_user_id", "oidc_refresh_tokens", ["user_id"]
    )
    op.create_index(
        "ix_oidc_refresh_tokens_client_id", "oidc_refresh_tokens", ["client_id"]
    )
    op.create_index(
        "ix_oidc_refresh_tokens_token_hash", "oidc_refresh_tokens", ["token_hash"]
    )
    op.create_index(
        "ix_oidc_refresh_tokens_family_id", "oidc_refresh_tokens", ["family_id"]
    )
    op.create_index(
        "ix_oidc_refresh_tokens_expires_at", "oidc_refresh_tokens", ["expires_at"]
    )


def downgrade():
    # Raw bearer values cannot be reconstructed from hashes. Revoke/delete all
    # token rows before restoring the legacy non-null plaintext columns.
    op.execute("DELETE FROM oidc_refresh_tokens")
    op.execute("DELETE FROM refresh_tokens")
    op.execute("DELETE FROM password_reset_token")
    op.drop_table("oidc_refresh_tokens")
    op.drop_table("authorization_codes")
    op.drop_table("oauth_clients")
    op.drop_index("ix_password_reset_token_token_hash", table_name="password_reset_token")
    op.drop_column("password_reset_token", "token_hash")
    op.alter_column("password_reset_token", "token", nullable=False)
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "token_hash")
    op.alter_column("refresh_tokens", "token", nullable=False)
    op.drop_index("ix_user_is_active", table_name="user")
    op.drop_column("user", "auth_version")
    op.drop_column("user", "is_active")
