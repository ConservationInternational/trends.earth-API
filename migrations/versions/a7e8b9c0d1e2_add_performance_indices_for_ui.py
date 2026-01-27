"""Add performance indices for API UI pages

Revision ID: a7e8b9c0d1e2
Revises: dfeac915776c
Create Date: 2026-01-27 12:00:00.000000

This migration adds additional database indices to optimize the API UI pages:
1. Scripts page: sorting by created_at, updated_at; filtering by public
2. Executions page: filtering/sorting by end_date
3. Users page: geographic distribution filtering by country
4. Admin page: rate limit event queries
5. Stats/Status dashboards: time-series aggregations

These indices complement the existing indices from dfeac915776c migration
and specifically target query patterns used by the trends.earth-api-ui
application's scripts, admin, and status pages.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a7e8b9c0d1e2"
down_revision = "dfeac915776c"
branch_labels = None
depends_on = None


def upgrade():
    # ============================================================
    # INDICES FOR EXECUTION PAGE QUERIES
    # ============================================================

    # Index on execution.end_date - for date filtering and duration calculations
    # Used by: executions page date filters, stats_service time-series
    op.create_index(
        "ix_execution_end_date",
        "execution",
        ["end_date"],
        unique=False,
        if_not_exists=True,
    )

    # Composite index for completed executions time-series
    # Used by: stats_service when bucketing completed tasks by end_date
    op.create_index(
        "ix_execution_status_end_date",
        "execution",
        ["status", "end_date"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDICES FOR SCRIPT PAGE QUERIES
    # ============================================================

    # Index on script.created_at - default sort order for scripts list
    # Used by: get_scripts() default ordering
    op.create_index(
        "ix_script_created_at",
        "script",
        ["created_at"],
        unique=False,
        if_not_exists=True,
    )

    # Index on script.updated_at - for sorting/filtering by update date
    # Used by: scripts page sorting and filtering
    op.create_index(
        "ix_script_updated_at",
        "script",
        ["updated_at"],
        unique=False,
        if_not_exists=True,
    )

    # Index on script.public - for access control filtering
    # Used by: get_scripts() access control queries
    op.create_index(
        "ix_script_public",
        "script",
        ["public"],
        unique=False,
        if_not_exists=True,
    )

    # Composite index for restricted script access control
    # Used by: get_scripts() when filtering by public + restricted
    op.create_index(
        "ix_script_public_restricted",
        "script",
        ["public", "restricted"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDICES FOR USER PAGE QUERIES
    # ============================================================

    # Index on user.country - for geographic distribution queries
    # Used by: stats_service._get_geographic_data()
    op.create_index(
        "ix_user_country",
        "user",
        ["country"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDICES FOR RATE LIMIT EVENT QUERIES (Admin page)
    # ============================================================

    # Index on rate_limit_event.occurred_at - for sorting and time filtering
    # Used by: list_events() sorting and since filtering
    op.create_index(
        "ix_rate_limit_event_occurred_at",
        "rate_limit_event",
        ["occurred_at"],
        unique=False,
        if_not_exists=True,
    )

    # Index on rate_limit_event.user_id - for filtering by user
    # Used by: list_active_rate_limits(), list_events()
    op.create_index(
        "ix_rate_limit_event_user_id",
        "rate_limit_event",
        ["user_id"],
        unique=False,
        if_not_exists=True,
    )

    # Index on rate_limit_event.limit_key - for deduplication lookups
    # Used by: record_event() duplicate detection
    op.create_index(
        "ix_rate_limit_event_limit_key",
        "rate_limit_event",
        ["limit_key"],
        unique=False,
        if_not_exists=True,
    )

    # Index on rate_limit_event.ip_address - for IP-based filtering
    # Used by: list_active_rate_limits(), list_events()
    op.create_index(
        "ix_rate_limit_event_ip_address",
        "rate_limit_event",
        ["ip_address"],
        unique=False,
        if_not_exists=True,
    )

    # Composite index for active rate limit queries
    # Used by: list_active_rate_limits() which filters by expires_at > now
    op.create_index(
        "ix_rate_limit_event_expires_at_type",
        "rate_limit_event",
        ["expires_at", "rate_limit_type"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDICES FOR STATUS LOG QUERIES (Status/Dashboard pages)
    # ============================================================

    # Composite index for status log transition queries
    # Used by: filtering status logs by status transitions
    op.create_index(
        "ix_status_log_status_from_to",
        "status_log",
        ["status_from", "status_to"],
        unique=False,
        if_not_exists=True,
    )


def downgrade():
    # Execution indices
    op.drop_index("ix_execution_end_date", table_name="execution")
    op.drop_index("ix_execution_status_end_date", table_name="execution")

    # Script indices
    op.drop_index("ix_script_created_at", table_name="script")
    op.drop_index("ix_script_updated_at", table_name="script")
    op.drop_index("ix_script_public", table_name="script")
    op.drop_index("ix_script_public_restricted", table_name="script")

    # User indices
    op.drop_index("ix_user_country", table_name="user")

    # Rate limit event indices
    op.drop_index("ix_rate_limit_event_occurred_at", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_user_id", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_limit_key", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_ip_address", table_name="rate_limit_event")
    op.drop_index("ix_rate_limit_event_expires_at_type", table_name="rate_limit_event")

    # Status log indices
    op.drop_index("ix_status_log_status_from_to", table_name="status_log")
