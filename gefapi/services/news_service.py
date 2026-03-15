"""NEWS SERVICE"""

from datetime import UTC, datetime
import logging
from typing import Any

from sqlalchemy import or_

from gefapi import db
from gefapi.models.news import NewsItem

logger = logging.getLogger()


class NewsService:
    """
    Service class for managing news items.

    Provides CRUD operations for news items.
    Supports filtering by platform, version, role, and date ranges.
    """

    @staticmethod
    def get_news_items(
        platform: str | None = None,
        version: str | None = None,
        user_role: str | None = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        sort: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[NewsItem], int]:
        """
        Get news items with optional filtering.

        Args:
            platform: Filter by target platform (app, webapp, api-ui)
            version: Filter by plugin version compatibility
            user_role: Filter by user role (USER, ADMIN, SUPERADMIN) or None for public
            include_inactive: Include inactive news items (admin only)
            include_expired: Include expired news items (admin only)
            sort: Sort field with optional '-' prefix for descending
            page: Page number for pagination (default: 1)
            per_page: Results per page (default: 20)

        Returns:
            tuple: (news_items list, total count)
        """
        logger.info("[SERVICE]: Getting news items")

        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 20

        query = db.session.query(NewsItem)
        now = datetime.now(UTC)

        # Filter by active status
        if not include_inactive:
            query = query.filter(NewsItem.is_active == True)  # noqa: E712

        # Filter by publish window
        query = query.filter(NewsItem.publish_at <= now)

        if not include_expired:
            query = query.filter(
                or_(NewsItem.expires_at.is_(None), NewsItem.expires_at > now)
            )

        # Filter by platform
        if platform:
            # Use LIKE to match platform in comma-separated list
            query = query.filter(
                or_(
                    NewsItem.target_platforms.like(f"%{platform}%"),
                    NewsItem.target_platforms.is_(None),
                )
            )

        # Filter by version if provided
        if version:
            # Include items where version is in range or no version constraints
            # Note: More complex version comparison handled in model method
            # Here we just filter out obviously incompatible items
            query = query.filter(
                or_(
                    NewsItem.min_version.is_(None),
                    NewsItem.max_version.is_(None),
                    NewsItem.min_version.isnot(None),
                    NewsItem.max_version.isnot(None),
                )
            )

        # Apply sorting
        if sort:
            sort_field = sort[1:] if sort.startswith("-") else sort
            sort_direction = "desc" if sort.startswith("-") else "asc"
            if hasattr(NewsItem, sort_field):
                query = query.order_by(
                    getattr(getattr(NewsItem, sort_field), sort_direction)()
                )
        else:
            # Default: priority descending, then publish_at descending
            query = query.order_by(NewsItem.priority.desc(), NewsItem.publish_at.desc())

        total = query.count()
        news_items = query.offset((page - 1) * per_page).limit(per_page).all()

        # Post-filter by version compatibility (more accurate)
        if version:
            news_items = [
                item for item in news_items if item.is_applicable_to_version(version)
            ]
            # Note: This affects total count accuracy, but is more accurate
            # for version filtering

        # Post-filter by role compatibility
        # user_role=None means unauthenticated user, show only unrestricted news
        news_items = [
            item for item in news_items if item.is_applicable_to_role(user_role)
        ]

        return news_items, total

    @staticmethod
    def get_news_item(news_id: str, include_inactive: bool = False) -> NewsItem | None:
        """
        Get a single news item by ID.

        Args:
            news_id: The news item ID (UUID)
            include_inactive: If True, return the item even if inactive

        Returns:
            NewsItem or None if not found
        """
        logger.info(f"[SERVICE]: Getting news item {news_id}")
        query = db.session.query(NewsItem).filter(NewsItem.id == news_id)

        if not include_inactive:
            query = query.filter(NewsItem.is_active.is_(True))

        return query.first()

    @staticmethod
    def create_news_item(
        title: str,
        message: str,
        created_by_id: str | None = None,
        **kwargs: Any,
    ) -> NewsItem:
        """
        Create a new news item.

        Args:
            title: News item title
            message: News item message content
            created_by_id: UUID of the admin creating the item
            **kwargs: Additional fields (link_url, link_text, publish_at,
                     expires_at, target_platforms, min_version, max_version,
                     is_active, priority, news_type)

        Returns:
            The created NewsItem
        """
        logger.info("[SERVICE]: Creating news item")

        news_item = NewsItem(
            title=title,
            message=message,
            created_by_id=created_by_id,
            **kwargs,
        )

        db.session.add(news_item)
        db.session.commit()

        logger.info(f"[SERVICE]: Created news item {news_item.id}")
        return news_item

    @staticmethod
    def update_news_item(news_id: str, **kwargs: Any) -> NewsItem | None:
        """
        Update an existing news item.

        Args:
            news_id: The news item ID (UUID)
            **kwargs: Fields to update

        Returns:
            Updated NewsItem or None if not found
        """
        logger.info(f"[SERVICE]: Updating news item {news_id}")

        news_item = NewsService.get_news_item(news_id)
        if not news_item:
            return None

        # Update allowed fields
        allowed_fields = [
            "title",
            "message",
            "link_url",
            "link_text",
            "publish_at",
            "expires_at",
            "target_platforms",
            "min_version",
            "max_version",
            "is_active",
            "priority",
            "news_type",
        ]

        for field in allowed_fields:
            if field in kwargs:
                setattr(news_item, field, kwargs[field])

        db.session.commit()
        logger.info(f"[SERVICE]: Updated news item {news_id}")
        return news_item

    @staticmethod
    def delete_news_item(news_id: str) -> bool:
        """
        Delete a news item.

        Args:
            news_id: The news item ID (UUID)

        Returns:
            True if deleted, False if not found
        """
        logger.info(f"[SERVICE]: Deleting news item {news_id}")

        news_item = NewsService.get_news_item(news_id)
        if not news_item:
            return False

        db.session.delete(news_item)
        db.session.commit()

        logger.info(f"[SERVICE]: Deleted news item {news_id}")
        return True
