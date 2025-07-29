"""REFRESH TOKEN CLEANUP TASKS"""

import logging

from celery import Task
import rollbar

logger = logging.getLogger(__name__)


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
        from gefapi.services.refresh_token_service import RefreshTokenService

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
