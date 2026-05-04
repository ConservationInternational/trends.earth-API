"""BULK EMAIL SEND TASK

Celery task that performs the actual SparkPost submission for a bulk email.

The HTTP route marks the BulkEmail as SENDING and dispatches this task,
returning 202 Accepted immediately.  This task runs in the Celery worker,
fetches recipients, generates per-recipient unsubscribe tokens, and calls
SparkPost in batches.  On completion the record is updated to SENT or FAILED.
"""

import logging

from celery import Task
import rollbar

logger = logging.getLogger(__name__)


class BulkEmailSendTask(Task):
    """Base task for bulk email send operations."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("Bulk email send task %s failed: %s", task_id, exc)
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency
from gefapi import celery  # noqa: E402


@celery.task(base=BulkEmailSendTask, bind=True)
def send_bulk_email_task(self, bulk_email_id: str, sent_by_user_id: str) -> None:
    """Send a bulk email via SparkPost, updating the record on completion.

    Parameters
    ----------
    bulk_email_id:     UUID string of the BulkEmail record (status=SENDING).
    sent_by_user_id:   UUID string of the user who triggered the send.
    """
    logger.info("[TASK]: Starting bulk email send for bulk_email_id=%s", bulk_email_id)

    from gefapi import app
    from gefapi.services.bulk_email_service import _execute_send

    with app.app_context():
        try:
            _execute_send(bulk_email_id, sent_by_user_id)
            logger.info(
                "[TASK]: Bulk email send completed for bulk_email_id=%s", bulk_email_id
            )
        except Exception as exc:
            logger.error(
                "[TASK]: Bulk email send failed for bulk_email_id=%s: %s",
                bulk_email_id,
                exc,
            )
            # _execute_send already sets status=FAILED and reports to Rollbar;
            # do not retry to avoid re-sending partially delivered batches.
            raise
