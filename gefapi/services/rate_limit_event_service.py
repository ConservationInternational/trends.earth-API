"""Service helpers for rate limit event persistence."""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import func, or_

from gefapi import db
from gefapi.models import RateLimitEvent

logger = logging.getLogger(__name__)


class RateLimitEventService:
    """Utility functions for recording and querying rate limit events."""

    @staticmethod
    def record_event(
        *,
        rate_limit_type: str,
        endpoint: str,
        method: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        user_email: str | None = None,
        limit_definition: str | None = None,
        limit_count: int | None = None,
        time_window_seconds: int | None = None,
        retry_after_seconds: int | None = None,
        limit_key: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> RateLimitEvent | None:
        """Persist a new rate limit event.

        Args:
            rate_limit_type: Categorisation for the breach (e.g. USER, IP, AUTH).
            endpoint: Request endpoint/path that triggered the limit.
            method: HTTP method used for the request.
            user_id: Optional ID of the authenticated user.
            user_role: Role of the user at the time of the breach.
            user_email: Email associated with the user (for historical context).
            limit_definition: String describing the rate limit rule breached.
            limit_count: Parsed numeric limit (requests allowed per window).
            time_window_seconds: Window length in seconds, when available.
            retry_after_seconds: Retry-After hint supplied by limiter.
            limit_key: Underlying limiter storage key or identifier.
            ip_address: Source IP address for the request if known.
            user_agent: User agent string supplied with the request.

        Returns:
            The created :class:`RateLimitEvent` instance, or ``None`` when the
            insert fails.
        """

        occurred_at = datetime.datetime.now(datetime.UTC)

        # Calculate when this rate limit will expire
        # Use retry_after_seconds if available (most accurate), otherwise fall back
        # to the configured time window
        expires_in_seconds = retry_after_seconds or time_window_seconds
        expires_at = None
        if expires_in_seconds:
            try:
                expires_at = occurred_at + datetime.timedelta(
                    seconds=int(expires_in_seconds)
                )
            except (TypeError, ValueError):
                logger.debug(
                    "Could not calculate expires_at from: %s", expires_in_seconds
                )

        dedupe_window_seconds = time_window_seconds or retry_after_seconds or 60
        try:
            dedupe_window_seconds = int(dedupe_window_seconds)
        except (TypeError, ValueError):
            dedupe_window_seconds = 60
        dedupe_window_seconds = max(dedupe_window_seconds, 1)

        cutoff = occurred_at - datetime.timedelta(seconds=dedupe_window_seconds)
        existing_event = None

        try:
            if limit_key:
                existing_event = (
                    RateLimitEvent.query.filter(
                        RateLimitEvent.limit_key == limit_key,
                        RateLimitEvent.occurred_at >= cutoff,
                    )
                    .order_by(RateLimitEvent.occurred_at.desc())
                    .first()
                )

            if existing_event is None and user_id:
                existing_event = (
                    RateLimitEvent.query.filter(
                        RateLimitEvent.user_id == user_id,
                        RateLimitEvent.endpoint == endpoint,
                        RateLimitEvent.occurred_at >= cutoff,
                    )
                    .order_by(RateLimitEvent.occurred_at.desc())
                    .first()
                )

            if existing_event is None and ip_address:
                existing_event = (
                    RateLimitEvent.query.filter(
                        RateLimitEvent.ip_address == ip_address,
                        RateLimitEvent.endpoint == endpoint,
                        RateLimitEvent.occurred_at >= cutoff,
                    )
                    .order_by(RateLimitEvent.occurred_at.desc())
                    .first()
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to evaluate rate limit duplicate: %s", exc)

        if existing_event:
            return existing_event

        event = RateLimitEvent(
            rate_limit_type=rate_limit_type,
            endpoint=endpoint,
            method=method,
            user_id=user_id,
            user_role=user_role,
            user_email=user_email,
            limit_definition=limit_definition,
            limit_count=limit_count,
            time_window_seconds=time_window_seconds,
            retry_after_seconds=retry_after_seconds,
            limit_key=limit_key,
            ip_address=ip_address,
            user_agent=user_agent,
            occurred_at=occurred_at,
            expires_at=expires_at,
        )

        try:
            db.session.add(event)
            db.session.commit()
            return event
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to record rate limit event: %s", exc, exc_info=True)
            db.session.rollback()
            return None

    @staticmethod
    def list_active_rate_limits(
        *,
        rate_limit_type: str | None = None,
        user_id: str | None = None,
        ip_address: str | None = None,
    ) -> list[RateLimitEvent]:
        """Fetch currently active rate limits (not yet expired).

        Args:
            rate_limit_type: Filter by a specific rate limit category.
            user_id: Filter by associated user identifier.
            ip_address: Filter by originating IP address.

        Returns:
            List of RateLimitEvent instances that are still active.
        """
        now = datetime.datetime.now(datetime.UTC)

        base_query = RateLimitEvent.query.filter(
            RateLimitEvent.expires_at.isnot(None),
            RateLimitEvent.expires_at > now,
        )

        if rate_limit_type:
            base_query = base_query.filter(
                RateLimitEvent.rate_limit_type == rate_limit_type
            )

        if user_id:
            base_query = base_query.filter(RateLimitEvent.user_id == user_id)

        if ip_address:
            base_query = base_query.filter(RateLimitEvent.ip_address == ip_address)

        # Order by expires_at descending to show most recently imposed limits first
        return list(base_query.order_by(RateLimitEvent.expires_at.desc()).all())

    @staticmethod
    def expire_events_for_identifier(identifier: str | None) -> int:
        """Force expire any active events tied to a specific identifier."""

        if not identifier:
            return 0

        identifier = identifier.strip()
        if not identifier:
            return 0

        now = datetime.datetime.now(datetime.UTC)
        filters = [RateLimitEvent.limit_key == identifier]

        if identifier.startswith("user:"):
            candidate = identifier.split("user:", 1)[1]
            try:
                user_uuid = uuid.UUID(candidate)
                filters.append(RateLimitEvent.user_id == user_uuid)
                filters.append(RateLimitEvent.user_id == str(user_uuid))
            except (ValueError, TypeError, AttributeError):
                if candidate:
                    filters.append(RateLimitEvent.user_id == candidate)

        if identifier.startswith("ip:"):
            ip_address = identifier.split("ip:", 1)[1]
            if ip_address:
                filters.append(RateLimitEvent.ip_address == ip_address)

        try:
            updated = RateLimitEvent.query.filter(
                RateLimitEvent.expires_at.isnot(None),
                RateLimitEvent.expires_at > now,
                or_(*filters),
            ).update({"expires_at": now}, synchronize_session=False)
            db.session.commit()
            return int(updated or 0)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug(
                "Failed to expire rate limit events for %s: %s", identifier, exc
            )
            db.session.rollback()
            return 0

    @staticmethod
    def expire_all_active_events() -> int:
        """Force expire all currently active rate limit events."""

        now = datetime.datetime.now(datetime.UTC)

        try:
            updated = RateLimitEvent.query.filter(
                RateLimitEvent.expires_at.isnot(None),
                RateLimitEvent.expires_at > now,
            ).update({"expires_at": now}, synchronize_session=False)
            db.session.commit()
            return int(updated or 0)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to expire all active rate limit events: %s", exc)
            db.session.rollback()
            return 0

    @staticmethod
    def list_events(
        *,
        limit: int = 100,
        offset: int = 0,
        since: datetime.datetime | None = None,
        rate_limit_type: str | None = None,
        user_id: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[list[RateLimitEvent], int]:
        """Fetch rate limit events with optional filters.

        Args:
            limit: Maximum number of entries to return.
            offset: Number of entries to skip (for pagination).
            since: Lower bound timestamp to filter events (UTC).
            rate_limit_type: Filter by a specific rate limit category.
            user_id: Filter by associated user identifier.
            ip_address: Filter by originating IP address.

        Returns:
            Tuple of (events, total_count) based on the supplied filters.
        """

        base_query = RateLimitEvent.query

        if since is not None:
            base_query = base_query.filter(RateLimitEvent.occurred_at >= since)

        if rate_limit_type:
            base_query = base_query.filter(
                RateLimitEvent.rate_limit_type == rate_limit_type
            )

        if user_id:
            base_query = base_query.filter(RateLimitEvent.user_id == user_id)

        if ip_address:
            base_query = base_query.filter(RateLimitEvent.ip_address == ip_address)

        total = base_query.with_entities(func.count()).scalar() or 0

        events_query = base_query.order_by(RateLimitEvent.occurred_at.desc())
        events = list(events_query.offset(offset).limit(limit).all())

        return events, int(total)
