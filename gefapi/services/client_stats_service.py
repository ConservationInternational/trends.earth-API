"""Client statistics service for aggregating platform usage data."""

from datetime import UTC, datetime, timedelta
import logging

from sqlalchemy import func

from gefapi import db
from gefapi.models.user_client_metadata import UserClientMetadata

logger = logging.getLogger(__name__)

DEFAULT_PERIOD = 30


class ClientStatsService:
    """Service for aggregating client platform and version statistics."""

    @staticmethod
    def get_client_stats(
        days: int = DEFAULT_PERIOD, client_type: str | None = None
    ) -> dict:
        """Get comprehensive client usage statistics.

        Args:
            days: Number of days to look back (any positive integer)
            client_type: Optional filter for specific client type

        Returns:
            Dict with platform_summary, plugin_stats, api_ui_stats, cli_stats
        """
        if days < 1:
            days = DEFAULT_PERIOD

        cutoff_date = datetime.now(UTC) - timedelta(days=days)

        try:
            result = {
                "period_days": days,
                "generated_at": datetime.now(UTC).isoformat(),
                "platform_summary": ClientStatsService._get_platform_summary(
                    cutoff_date
                ),
                "language_stats": ClientStatsService._get_language_distribution(
                    cutoff_date
                ),
            }

            # Add platform-specific stats
            if client_type is None or client_type == "qgis_plugin":
                result["plugin_stats"] = ClientStatsService._get_plugin_stats(
                    cutoff_date
                )

            if client_type is None or client_type == "api_ui":
                result["api_ui_stats"] = ClientStatsService._get_simple_version_stats(
                    "api_ui", cutoff_date
                )

            if client_type is None or client_type == "cli":
                result["cli_stats"] = ClientStatsService._get_simple_version_stats(
                    "cli", cutoff_date
                )

            return result

        except Exception as e:
            logger.error(f"[ClientStats] Error getting stats: {e}")
            raise

    @staticmethod
    def _get_platform_summary(cutoff_date: datetime) -> dict:
        """Get summary of active users per platform."""
        # Query for active users (within period) and total users per platform
        query = db.session.query(
            UserClientMetadata.client_type,
            func.count().label("total_users"),
            func.count()
            .filter(UserClientMetadata.last_seen_at >= cutoff_date)
            .label("active_users"),
        ).group_by(UserClientMetadata.client_type)

        result = {}
        for row in query.all():
            result[row.client_type] = {
                "active_users": row.active_users,
                "total_users": row.total_users,
            }

        return result

    @staticmethod
    def _get_plugin_stats(cutoff_date: datetime) -> dict:
        """Get detailed plugin statistics with cross-tabulation."""
        # Plugin versions with QGIS and OS breakdown
        by_plugin_version = ClientStatsService._get_plugin_version_breakdown(
            cutoff_date
        )

        # QGIS versions with plugin version breakdown
        by_qgis_version = ClientStatsService._get_qgis_version_breakdown(cutoff_date)

        # OS distribution (simple)
        by_os = ClientStatsService._get_os_distribution(cutoff_date)

        # Language distribution
        by_language = ClientStatsService._get_language_distribution(cutoff_date)

        return {
            "by_plugin_version": by_plugin_version,
            "by_qgis_version": by_qgis_version,
            "by_os": by_os,
            "by_language": by_language,
        }

    @staticmethod
    def _get_plugin_version_breakdown(cutoff_date: datetime) -> list:
        """Get plugin version stats with QGIS version and OS breakdown."""
        # First, get all plugin versions with counts
        version_query = (
            db.session.query(
                UserClientMetadata.client_version,
                func.count().label("user_count"),
            )
            .filter(
                UserClientMetadata.client_type == "qgis_plugin",
                UserClientMetadata.last_seen_at >= cutoff_date,
            )
            .group_by(UserClientMetadata.client_version)
            .order_by(func.count().desc())
        )

        versions = []
        for row in version_query.all():
            version = row.client_version or "unknown"

            # Get QGIS version breakdown for this plugin version
            qgis_breakdown = (
                db.session.query(
                    UserClientMetadata.qgis_version,
                    func.count().label("count"),
                )
                .filter(
                    UserClientMetadata.client_type == "qgis_plugin",
                    UserClientMetadata.client_version == row.client_version,
                    UserClientMetadata.last_seen_at >= cutoff_date,
                )
                .group_by(UserClientMetadata.qgis_version)
                .order_by(func.count().desc())
                .all()
            )

            # Get OS breakdown for this plugin version
            os_breakdown = (
                db.session.query(
                    UserClientMetadata.os,
                    func.count().label("count"),
                )
                .filter(
                    UserClientMetadata.client_type == "qgis_plugin",
                    UserClientMetadata.client_version == row.client_version,
                    UserClientMetadata.last_seen_at >= cutoff_date,
                )
                .group_by(UserClientMetadata.os)
                .order_by(func.count().desc())
                .all()
            )

            versions.append(
                {
                    "version": version,
                    "user_count": row.user_count,
                    "by_qgis_version": [
                        {"qgis_version": r.qgis_version or "unknown", "count": r.count}
                        for r in qgis_breakdown
                    ],
                    "by_os": [
                        {"os": r.os or "unknown", "count": r.count}
                        for r in os_breakdown
                    ],
                }
            )

        return versions

    @staticmethod
    def _get_qgis_version_breakdown(cutoff_date: datetime) -> list:
        """Get QGIS version stats with plugin version breakdown."""
        # Get all QGIS versions with counts
        qgis_query = (
            db.session.query(
                UserClientMetadata.qgis_version,
                func.count().label("user_count"),
            )
            .filter(
                UserClientMetadata.client_type == "qgis_plugin",
                UserClientMetadata.last_seen_at >= cutoff_date,
            )
            .group_by(UserClientMetadata.qgis_version)
            .order_by(func.count().desc())
        )

        versions = []
        for row in qgis_query.all():
            qgis_version = row.qgis_version or "unknown"

            # Get plugin version breakdown for this QGIS version
            plugin_breakdown = (
                db.session.query(
                    UserClientMetadata.client_version,
                    func.count().label("count"),
                )
                .filter(
                    UserClientMetadata.client_type == "qgis_plugin",
                    UserClientMetadata.qgis_version == row.qgis_version,
                    UserClientMetadata.last_seen_at >= cutoff_date,
                )
                .group_by(UserClientMetadata.client_version)
                .order_by(func.count().desc())
                .all()
            )

            versions.append(
                {
                    "qgis_version": qgis_version,
                    "user_count": row.user_count,
                    "by_plugin_version": [
                        {"version": r.client_version or "unknown", "count": r.count}
                        for r in plugin_breakdown
                    ],
                }
            )

        return versions

    @staticmethod
    def _get_os_distribution(cutoff_date: datetime) -> list:
        """Get simple OS distribution for plugin users."""
        query = (
            db.session.query(
                UserClientMetadata.os,
                func.count().label("user_count"),
            )
            .filter(
                UserClientMetadata.client_type == "qgis_plugin",
                UserClientMetadata.last_seen_at >= cutoff_date,
            )
            .group_by(UserClientMetadata.os)
            .order_by(func.count().desc())
        )

        return [
            {"os": row.os or "unknown", "user_count": row.user_count}
            for row in query.all()
        ]

    @staticmethod
    def _get_language_distribution(cutoff_date: datetime) -> list:
        """Get language distribution across all clients."""
        query = (
            db.session.query(
                UserClientMetadata.language,
                func.count().label("user_count"),
            )
            .filter(
                UserClientMetadata.last_seen_at >= cutoff_date,
                UserClientMetadata.language.isnot(None),
            )
            .group_by(UserClientMetadata.language)
            .order_by(func.count().desc())
        )

        return [
            {"language": row.language, "user_count": row.user_count}
            for row in query.all()
        ]

    @staticmethod
    def _get_simple_version_stats(client_type: str, cutoff_date: datetime) -> dict:
        """Get simple version stats for non-plugin clients."""
        query = (
            db.session.query(
                UserClientMetadata.client_version,
                func.count().label("user_count"),
            )
            .filter(
                UserClientMetadata.client_type == client_type,
                UserClientMetadata.last_seen_at >= cutoff_date,
            )
            .group_by(UserClientMetadata.client_version)
            .order_by(func.count().desc())
        )

        return {
            "by_version": [
                {
                    "version": row.client_version or "unknown",
                    "user_count": row.user_count,
                }
                for row in query.all()
            ]
        }
