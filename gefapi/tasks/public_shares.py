"""Validation and cleanup workers for expiring public web-map shares."""

from datetime import UTC, datetime, timedelta
import logging

import botocore
from celery import Task
from sqlalchemy.exc import SQLAlchemyError

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import PublicShare

logger = logging.getLogger(__name__)


class PublicShareTask(Task):
    """Log worker failures without logging signed URLs or capability secrets."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(
            "Public-share task failed (task_id=%s, share_id=%s): %s",
            task_id,
            args[0] if args else "unknown",
            exc,
        )
        if (
            self.name != "gefapi.tasks.public_shares.validate_public_share_task"
            or not args
        ):
            return

        from gefapi import app

        with app.app_context():
            share = PublicShare.query.filter_by(id=str(args[0])).first()
            if share is None or (
                share.state != "validating" and share.update_state != "validating"
            ):
                return
            is_additive_update = share.state == "ready"
            if is_additive_update:
                share.update_state = "failed"
            else:
                share.state = "failed"
            for layer in share.layers:
                if is_additive_update and layer.published:
                    continue
                if layer.validation_error is None:
                    layer.validation_error = (
                        "Validation could not be completed; upload and retry"
                    )
            db.session.commit()


from gefapi import celery  # noqa: E402


@celery.task(
    base=PublicShareTask,
    bind=True,
    autoretry_for=(
        OSError,
        botocore.exceptions.BotoCoreError,
        botocore.exceptions.ClientError,
    ),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def validate_public_share_task(self, share_id):
    """Validate COG/PMTiles checksums and structure before owner publication."""
    from gefapi import app
    from gefapi.services.public_share_service import validate_share

    with app.app_context():
        validate_share(share_id)


@celery.task(base=PublicShareTask, bind=True)
def cleanup_public_shares_task(self):
    """Expire and clean a bounded batch of revoked or expired shares."""
    from gefapi import app
    from gefapi.services.public_share_service import (
        ACTIVE_STATES,
        cleanup_share_objects,
    )

    with app.app_context():
        now = datetime.now(UTC).replace(tzinfo=None)
        draft_cutoff = now - timedelta(
            seconds=SETTINGS["PUBLIC_SHARES"]["DRAFT_TTL_SECONDS"]
        )
        shares = (
            PublicShare.query.filter(
                PublicShare.cleanup_completed_at.is_(None),
                db.or_(
                    PublicShare.state.in_(("revoked", "expired")),
                    db.and_(
                        PublicShare.state == "ready",
                        PublicShare.expires_at.isnot(None),
                        PublicShare.expires_at <= now,
                    ),
                    db.and_(
                        PublicShare.state.in_(ACTIVE_STATES),
                        PublicShare.ready_at.is_(None),
                        PublicShare.created_at <= draft_cutoff,
                    ),
                ),
            )
            .order_by(PublicShare.created_at)
            .limit(100)
            .all()
        )
        cleaned = 0
        failures = 0
        for share in shares:
            share_id = str(share.id)
            try:
                cleanup_share_objects(share_id)
                cleaned += 1
            except (botocore.exceptions.BotoCoreError, SQLAlchemyError):
                db.session.rollback()
                failures += 1
                logger.exception("Could not clean public share %s", share_id)
        return {"cleaned": cleaned, "failures": failures}
