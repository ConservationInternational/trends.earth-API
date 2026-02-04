"""DELETION AUDIT CLEANUP TASKS

Periodic tasks for maintaining GDPR compliance in deletion audit records.

These tasks ensure that email hashes are cleared from deletion audit records
after the retention period expires (30 days for user-requested deletions).
"""

import logging

from celery import Task
import rollbar

logger = logging.getLogger(__name__)


class DeletionAuditCleanupTask(Task):
    """Base task for deletion audit cleanup operations"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Deletion audit cleanup task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=DeletionAuditCleanupTask, bind=True)
def cleanup_expired_email_hashes(self):
    """Clean up expired email hashes from deletion audit records.

    This task ensures GDPR compliance by removing email hashes
    after the retention period expires (30 days for user-requested deletions).

    Should be scheduled to run daily.
    """
    logger.info("[TASK]: Starting cleanup of expired email hashes in deletion audit")

    try:
        from gefapi import app
        from gefapi.models import UserDeletionAudit

        with app.app_context():
            cleared_count = UserDeletionAudit.cleanup_expired_hashes()

            logger.info(
                f"[TASK]: Cleared {cleared_count} expired email hashes "
                "from deletion audit records"
            )

            # Report to Rollbar for visibility
            if cleared_count > 0:
                rollbar.report_message(
                    f"Deletion audit cleanup: Cleared {cleared_count} expired "
                    "email hashes",
                    level="info",
                    extra_data={
                        "task": "cleanup_expired_email_hashes",
                        "cleared_count": cleared_count,
                    },
                )

            return {
                "status": "success",
                "cleared_count": cleared_count,
                "message": f"Cleared {cleared_count} expired email hashes",
            }

    except Exception as error:
        logger.error(f"[TASK]: Error cleaning up expired email hashes: {str(error)}")
        raise self.retry(exc=error, countdown=60, max_retries=3) from error


@celery.task(base=DeletionAuditCleanupTask, bind=True)
def get_deletion_audit_stats(self):
    """Get statistics about deletion audit records.

    This is a read-only task useful for monitoring and compliance reporting.
    """
    logger.info("[TASK]: Gathering deletion audit statistics")

    try:
        from sqlalchemy import func

        from gefapi import app, db
        from gefapi.models import UserDeletionAudit

        with app.app_context():
            # Total deletion records
            total_records = UserDeletionAudit.query.count()

            # Records by deletion reason
            reason_counts = (
                db.session.query(
                    UserDeletionAudit.deletion_reason,
                    func.count(UserDeletionAudit.id).label("count"),
                )
                .group_by(UserDeletionAudit.deletion_reason)
                .all()
            )
            reasons = dict(reason_counts)

            # Records with pending email hash cleanup
            pending_hash_cleanup = UserDeletionAudit.query.filter(
                UserDeletionAudit.email_hash.isnot(None),
                UserDeletionAudit.email_hash_expires_at.isnot(None),
            ).count()

            # Top countries by deletions
            country_counts = (
                db.session.query(
                    UserDeletionAudit.country,
                    func.count(UserDeletionAudit.id).label("count"),
                )
                .filter(UserDeletionAudit.country.isnot(None))
                .group_by(UserDeletionAudit.country)
                .order_by(func.count(UserDeletionAudit.id).desc())
                .limit(10)
                .all()
            )
            top_countries = dict(country_counts)

            # Average account age at deletion
            avg_age_result = db.session.query(
                func.avg(UserDeletionAudit.account_age_days)
            ).scalar()
            avg_account_age_days = (
                round(float(avg_age_result), 1) if avg_age_result else None
            )

            # Average executions per deleted user
            avg_executions_result = db.session.query(
                func.avg(UserDeletionAudit.total_executions)
            ).scalar()
            avg_executions = (
                round(float(avg_executions_result), 1)
                if avg_executions_result
                else None
            )

            stats = {
                "total_deletion_records": total_records,
                "deletions_by_reason": reasons,
                "pending_hash_cleanup": pending_hash_cleanup,
                "top_countries": top_countries,
                "average_account_age_days": avg_account_age_days,
                "average_executions_per_user": avg_executions,
            }

            logger.info(f"[TASK]: Deletion audit stats: {stats}")
            return stats

    except Exception as error:
        logger.error(f"[TASK]: Error gathering deletion audit stats: {str(error)}")
        raise self.retry(exc=error, countdown=60, max_retries=3) from error
