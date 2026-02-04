"""USER CLEANUP TASKS

Periodic tasks for cleaning up inactive or unverified user accounts.

These tasks delete users who:
1. Have email_verified = False and haven't logged in (after grace period)
2. Have never logged in at all (after longer grace period)

Existing users were marked as verified during the migration that added
these fields, so they won't be affected by cleanup tasks.
"""

import datetime
import json
import logging
import os

from celery import Task
import rollbar

logger = logging.getLogger(__name__)

# Number of days a user can remain unverified before cleanup
DEFAULT_UNVERIFIED_USER_CLEANUP_DAYS = 60

# Number of days of inactivity before a user is considered for cleanup
DEFAULT_INACTIVE_USER_CLEANUP_DAYS = 365


class UserCleanupTask(Task):
    """Base task for user cleanup operations"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"User cleanup task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=UserCleanupTask, bind=True)
def cleanup_unverified_users(self):
    """Clean up user accounts that have never verified their email address.

    Deletes users where:
    - email_verified = False
    - Account is older than UNVERIFIED_USER_CLEANUP_DAYS
    - User has never logged in (last_login_at is NULL)

    Configuration:
        UNVERIFIED_USER_CLEANUP_DAYS: Days before unverified users are deleted
                                      (default: 60)
    """
    cleanup_days = int(
        os.getenv("UNVERIFIED_USER_CLEANUP_DAYS", DEFAULT_UNVERIFIED_USER_CLEANUP_DAYS)
    )

    logger.info(
        f"[TASK]: Starting cleanup of unverified users "
        f"(unverified for {cleanup_days}+ days)"
    )

    try:
        from gefapi import app
        from gefapi.models import DeletionReason, User
        from gefapi.services.user_service import UserService

        with app.app_context():
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(
                days=cleanup_days
            )

            # Find users that:
            # 1. Have email_verified = False (explicitly unverified)
            # 2. Were created more than cleanup_days ago
            # 3. Have never logged in (no last_login_at)
            unverified_users = User.query.filter(
                User.email_verified.is_(False),
                User.created_at < cutoff_date,
                User.last_login_at.is_(None),
            ).all()

            deleted_count = 0
            deleted_emails = []

            for user in unverified_users:
                try:
                    email = user.email
                    # Create context for audit record
                    context = json.dumps(
                        {
                            "cleanup_task": "cleanup_unverified_users",
                            "threshold_days": cleanup_days,
                        }
                    )
                    UserService.delete_user(
                        user.id,
                        deletion_reason=DeletionReason.UNVERIFIED_EMAIL,
                        context=context,
                    )
                    deleted_count += 1
                    deleted_emails.append(email)
                    logger.info(
                        f"[TASK]: Deleted unverified user: {email} "
                        f"(created: {user.created_at})"
                    )
                except Exception as e:
                    logger.error(
                        f"[TASK]: Failed to delete unverified user {user.email}: {e}"
                    )
                    continue

            logger.info(f"[TASK]: Cleaned up {deleted_count} unverified user accounts")

            # Report to Rollbar for visibility
            if deleted_count > 0:
                rollbar.report_message(
                    f"User cleanup: Deleted {deleted_count} unverified user accounts",
                    level="info",
                    extra_data={
                        "task": "cleanup_unverified_users",
                        "deleted_count": deleted_count,
                        "cleanup_days_threshold": cleanup_days,
                        "sample_emails": deleted_emails[:5],
                    },
                )

            return {
                "status": "success",
                "deleted_count": deleted_count,
                "cleanup_days_threshold": cleanup_days,
                "deleted_emails": deleted_emails[:10],  # Limit for logging
                "message": f"Deleted {deleted_count} unverified user accounts",
            }

    except Exception as error:
        logger.error(f"[TASK]: Error cleaning up unverified users: {str(error)}")
        raise self.retry(exc=error, countdown=60, max_retries=3) from error


@celery.task(base=UserCleanupTask, bind=True)
def cleanup_never_logged_in_users(self):
    """Clean up user accounts that have never logged in.

    This is a more aggressive cleanup for users who created accounts
    but never actually used them.

    Configuration:
        INACTIVE_USER_CLEANUP_DAYS: Days before never-logged-in users are deleted
                                    (default: 365)
    """
    cleanup_days = int(
        os.getenv("INACTIVE_USER_CLEANUP_DAYS", DEFAULT_INACTIVE_USER_CLEANUP_DAYS)
    )

    logger.info(
        f"[TASK]: Starting cleanup of users who never logged in "
        f"(account age > {cleanup_days} days)"
    )

    try:
        from gefapi import app
        from gefapi.models import DeletionReason, User
        from gefapi.services.user_service import UserService

        with app.app_context():
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(
                days=cleanup_days
            )

            # Find users that:
            # 1. Have never logged in (last_login_at is NULL)
            # 2. Were created more than cleanup_days ago
            never_logged_in_users = User.query.filter(
                User.last_login_at.is_(None),
                User.created_at < cutoff_date,
            ).all()

            deleted_count = 0
            deleted_emails = []

            for user in never_logged_in_users:
                try:
                    email = user.email
                    # Create context for audit record
                    context = json.dumps(
                        {
                            "cleanup_task": "cleanup_never_logged_in_users",
                            "threshold_days": cleanup_days,
                        }
                    )
                    UserService.delete_user(
                        user.id,
                        deletion_reason=DeletionReason.NEVER_LOGGED_IN,
                        context=context,
                    )
                    deleted_count += 1
                    deleted_emails.append(email)
                    logger.info(
                        f"[TASK]: Deleted never-logged-in user: {email} "
                        f"(created: {user.created_at})"
                    )
                except Exception as e:
                    logger.error(
                        f"[TASK]: Failed to delete never-logged-in user "
                        f"{user.email}: {e}"
                    )
                    continue

            logger.info(
                f"[TASK]: Cleaned up {deleted_count} never-logged-in user accounts"
            )

            # Report to Rollbar for visibility
            if deleted_count > 0:
                rollbar.report_message(
                    f"User cleanup: Deleted {deleted_count} never-logged-in users",
                    level="info",
                    extra_data={
                        "task": "cleanup_never_logged_in_users",
                        "deleted_count": deleted_count,
                        "cleanup_days_threshold": cleanup_days,
                        "sample_emails": deleted_emails[:5],
                    },
                )

            return {
                "status": "success",
                "deleted_count": deleted_count,
                "cleanup_days_threshold": cleanup_days,
                "deleted_emails": deleted_emails[:10],  # Limit for logging
                "message": f"Deleted {deleted_count} never-logged-in user accounts",
            }

    except Exception as error:
        logger.error(f"[TASK]: Error cleaning up never-logged-in users: {str(error)}")
        raise self.retry(exc=error, countdown=60, max_retries=3) from error


@celery.task(base=UserCleanupTask, bind=True)
def get_user_cleanup_stats(self):
    """Get statistics about users eligible for cleanup.

    This is a read-only task useful for monitoring and dry-run testing
    before enabling cleanup tasks.
    """
    unverified_days = int(
        os.getenv("UNVERIFIED_USER_CLEANUP_DAYS", DEFAULT_UNVERIFIED_USER_CLEANUP_DAYS)
    )
    inactive_days = int(
        os.getenv("INACTIVE_USER_CLEANUP_DAYS", DEFAULT_INACTIVE_USER_CLEANUP_DAYS)
    )

    logger.info("[TASK]: Gathering user cleanup statistics")

    try:
        from gefapi import app
        from gefapi.models import User

        with app.app_context():
            unverified_cutoff = datetime.datetime.utcnow() - datetime.timedelta(
                days=unverified_days
            )
            inactive_cutoff = datetime.datetime.utcnow() - datetime.timedelta(
                days=inactive_days
            )

            # Count total users
            total_users = User.query.count()

            # Count unverified users eligible for cleanup
            unverified_eligible = User.query.filter(
                User.email_verified.is_(False),
                User.created_at < unverified_cutoff,
                User.last_login_at.is_(None),
            ).count()

            # Count never-logged-in users eligible for cleanup
            never_logged_in_eligible = User.query.filter(
                User.last_login_at.is_(None),
                User.created_at < inactive_cutoff,
            ).count()

            # Count verified users
            verified_users = User.query.filter(User.email_verified.is_(True)).count()

            # Count users who have logged in
            logged_in_users = User.query.filter(User.last_login_at.isnot(None)).count()

            # Count unverified users (not eligible yet, but unverified)
            unverified_users = User.query.filter(User.email_verified.is_(False)).count()

            stats = {
                "total_users": total_users,
                "verified_users": verified_users,
                "unverified_users": unverified_users,
                "logged_in_users": logged_in_users,
                "unverified_eligible_for_cleanup": unverified_eligible,
                "never_logged_in_eligible_for_cleanup": never_logged_in_eligible,
                "unverified_cleanup_days": unverified_days,
                "inactive_cleanup_days": inactive_days,
            }

            logger.info(f"[TASK]: User cleanup stats: {stats}")
            return stats

    except Exception as error:
        logger.error(f"[TASK]: Error gathering user cleanup stats: {str(error)}")
        raise self.retry(exc=error, countdown=60, max_retries=3) from error
