"""Add indexes for faster API operations

Revision ID: dfeac915776c
Revises: 2c4f8e1a9b3d
Create Date: 2026-01-27 02:45:00.000000

This migration adds indexes to optimize:
1. User deletion (cascading deletes through related tables)
2. Execution listing/filtering (status, date, user)
3. Status log queries for monitoring dashboards
4. User authentication lookups
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "dfeac915776c"
down_revision = "2c4f8e1a9b3d"
branch_labels = None
depends_on = None


def upgrade():
    # ============================================================
    # INDEXES FOR USER DELETION (cascading deletes)
    # PostgreSQL does NOT auto-create indexes on foreign key columns
    # ============================================================

    # Index on execution.user_id - critical for finding user's executions
    op.create_index(
        "ix_execution_user_id",
        "execution",
        ["user_id"],
        unique=False,
        if_not_exists=True,
    )

    # Index on script.user_id - critical for finding user's scripts
    op.create_index(
        "ix_script_user_id",
        "script",
        ["user_id"],
        unique=False,
        if_not_exists=True,
    )

    # Index on status_log.execution_id - for deleting status logs by execution
    op.create_index(
        "ix_status_log_execution_id",
        "status_log",
        ["execution_id"],
        unique=False,
        if_not_exists=True,
    )

    # Index on execution_log.execution_id - for deleting execution logs
    op.create_index(
        "ix_execution_log_execution_id",
        "execution_log",
        ["execution_id"],
        unique=False,
        if_not_exists=True,
    )

    # Index on script_log.script_id - for deleting script logs
    op.create_index(
        "ix_script_log_script_id",
        "script_log",
        ["script_id"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDEXES FOR EXECUTION LISTING/FILTERING (API UI dashboard)
    # ============================================================

    # Index on execution.status - frequently filtered (PENDING, RUNNING, etc.)
    op.create_index(
        "ix_execution_status",
        "execution",
        ["status"],
        unique=False,
        if_not_exists=True,
    )

    # Index on execution.start_date - for date filtering and sorting
    op.create_index(
        "ix_execution_start_date",
        "execution",
        ["start_date"],
        unique=False,
        if_not_exists=True,
    )

    # Composite index for common query: status + start_date
    op.create_index(
        "ix_execution_status_start_date",
        "execution",
        ["status", "start_date"],
        unique=False,
        if_not_exists=True,
    )

    # Index on execution.script_id - for finding executions by script
    op.create_index(
        "ix_execution_script_id",
        "execution",
        ["script_id"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDEXES FOR STATUS LOG MONITORING (dashboard graphs)
    # ============================================================

    # Index on status_log.timestamp - for time-series queries
    op.create_index(
        "ix_status_log_timestamp",
        "status_log",
        ["timestamp"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDEXES FOR USER OPERATIONS
    # ============================================================

    # Index on user.created_at - for sorting users list
    op.create_index(
        "ix_user_created_at",
        "user",
        ["created_at"],
        unique=False,
        if_not_exists=True,
    )

    # Index on user.role - for filtering by role
    op.create_index(
        "ix_user_role",
        "user",
        ["role"],
        unique=False,
        if_not_exists=True,
    )

    # ============================================================
    # INDEXES FOR SCRIPT OPERATIONS
    # ============================================================

    # Index on script.status - for filtering scripts by status
    op.create_index(
        "ix_script_status",
        "script",
        ["status"],
        unique=False,
        if_not_exists=True,
    )


def downgrade():
    # User deletion indexes
    op.drop_index("ix_execution_user_id", table_name="execution")
    op.drop_index("ix_script_user_id", table_name="script")
    op.drop_index("ix_status_log_execution_id", table_name="status_log")
    op.drop_index("ix_execution_log_execution_id", table_name="execution_log")
    op.drop_index("ix_script_log_script_id", table_name="script_log")

    # Execution listing indexes
    op.drop_index("ix_execution_status", table_name="execution")
    op.drop_index("ix_execution_start_date", table_name="execution")
    op.drop_index("ix_execution_status_start_date", table_name="execution")
    op.drop_index("ix_execution_script_id", table_name="execution")

    # Status log indexes
    op.drop_index("ix_status_log_timestamp", table_name="status_log")

    # User indexes
    op.drop_index("ix_user_created_at", table_name="user")
    op.drop_index("ix_user_role", table_name="user")

    # Script indexes
    op.drop_index("ix_script_status", table_name="script")
    op.drop_index("ix_script_log_script_id", table_name="script_log")
