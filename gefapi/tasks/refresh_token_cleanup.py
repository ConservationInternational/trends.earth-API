"""REFRESH TOKEN CLEANUP TASKS"""

import datetime
import logging
import os

from celery import Task
import rollbar

logger = logging.getLogger(__name__)

# Default inactivity threshold: revoke tokens unused for 14 days
# Note: Refresh tokens expire after 30 days (JWT_REFRESH_TOKEN_EXPIRES),
# so this should be less than 30 to have any effect
DEFAULT_INACTIVE_TOKEN_DAYS = 14


class RefreshTokenCleanupTask(Task):
    """Base task for refresh token cleanup"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Refresh token cleanup task failed: {exc}")
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=RefreshTokenCleanupTask, bind=True)
def cleanup_expired_refresh_tokens(self):
    """Celery task to clean up expired refresh tokens"""
    logger.info("[TASK]: Starting cleanup of expired refresh tokens")

    try:
        from gefapi import app
        from gefapi.services.refresh_token_service import RefreshTokenService

        with app.app_context():
            cleaned_count = RefreshTokenService.cleanup_expired_tokens()

        logger.info(
            f"[TASK]: Successfully cleaned up {cleaned_count} expired refresh tokens"
        )
        return {
            "status": "success",
            "cleaned_count": cleaned_count,
            "message": f"Cleaned up {cleaned_count} expired refresh tokens",
        }
    except Exception as error:
        logger.error(f"[TASK]: Error cleaning up expired refresh tokens: {str(error)}")
        raise self.retry(exc=error, countdown=60, max_retries=3) from error


@celery.task(base=RefreshTokenCleanupTask, bind=True)
def cleanup_inactive_refresh_tokens(self):
    """Celery task to revoke refresh tokens not used for a configurable duration.

    This provides additional security by ensuring tokens that haven't been
    actively used are revoked, even if they haven't technically expired yet.

    Configuration:
        INACTIVE_TOKEN_DAYS: Environment variable to set inactivity threshold
                            (default: 90 days)
    """
    inactive_days = int(os.getenv("INACTIVE_TOKEN_DAYS", DEFAULT_INACTIVE_TOKEN_DAYS))
    logger.info(f"[TASK]: Starting cleanup of tokens inactive for {inactive_days} days")

    try:
        from gefapi import app, db
        from gefapi.models.refresh_token import RefreshToken

        with app.app_context():
            cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(
                days=inactive_days
            )

            # Find tokens that:
            # 1. Are not already revoked
            # 2. Have a last_used_at timestamp older than the cutoff
            # Note: Tokens with NULL last_used_at are likely old tokens created
            # before last_used_at tracking - we leave these alone for backwards
            # compatibility. They will eventually be cleaned up by the expired
            # token cleanup task.
            inactive_tokens = RefreshToken.query.filter(
                RefreshToken.is_revoked.is_(False),
                RefreshToken.last_used_at.isnot(None),
                RefreshToken.last_used_at < cutoff_date,
            ).all()

            revoked_count = 0
            for token in inactive_tokens:
                token.is_revoked = True
                revoked_count += 1

            if revoked_count > 0:
                db.session.commit()

            logger.info(
                f"[TASK]: Revoked {revoked_count} inactive refresh tokens "
                f"(unused for {inactive_days}+ days)"
            )

            # Report to Rollbar for visibility
            if revoked_count > 0:
                rollbar.report_message(
                    f"Token cleanup: Revoked {revoked_count} inactive refresh tokens",
                    level="info",
                    extra_data={
                        "task": "cleanup_inactive_refresh_tokens",
                        "revoked_count": revoked_count,
                        "inactive_days_threshold": inactive_days,
                    },
                )

            return {
                "status": "success",
                "revoked_count": revoked_count,
                "inactive_days_threshold": inactive_days,
                "message": f"Revoked {revoked_count} inactive refresh tokens",
            }

    except Exception as error:
        logger.error(f"[TASK]: Error revoking inactive refresh tokens: {str(error)}")
        raise self.retry(exc=error, countdown=60, max_retries=3) from error
