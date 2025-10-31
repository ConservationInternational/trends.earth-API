"""STATUS SERVICE"""

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from sqlalchemy import func

from gefapi import db
from gefapi.models import StatusLog

logger = logging.getLogger()


class StatusService:
    """
    Service class for retrieving and managing status log data.

    This service provides access to status log entries which track both
    execution status transitions and system monitoring snapshots.
    Supports filtering, sorting, and pagination for status log queries.
    """

    @staticmethod
    def resolve_period_bounds(
        period: str | None,
    ) -> tuple[datetime | None, datetime | None]:
        """Translate a period keyword into concrete datetime boundaries."""

        if not period or period == "all":
            return None, None

        now = datetime.now(UTC)
        period_starts = {
            "last_day": now - timedelta(days=1),
            "last_week": now - timedelta(days=7),
            "last_month": now - timedelta(days=30),
            "last_year": now - timedelta(days=365),
        }

        start = period_starts.get(period)
        return start, now

    @staticmethod
    def get_status_logs(
        start_date=None,
        end_date=None,
        sort=None,
        page=1,
        per_page=100,
    ):
        """
        Get status logs with optional filtering and pagination.

        Retrieves status log entries which may include both execution status
        transitions and system monitoring snapshots.

        Args:
            start_date (datetime, optional): Filter logs after this timestamp
            end_date (datetime, optional): Filter logs before this timestamp
            sort (str, optional): Sort field with optional '-' prefix for descending
            page (int): Page number for pagination (default: 1)
            per_page (int): Results per page (default: 100)

        Returns:
            tuple: (status_logs list, total count)
        """
        logger.info("[SERVICE]: Getting status logs")
        logger.info("[DB]: QUERY")

        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 100

        query = db.session.query(StatusLog)

        # Apply date filters
        if start_date:
            query = query.filter(StatusLog.timestamp >= start_date)
        if end_date:
            query = query.filter(StatusLog.timestamp <= end_date)

        # Apply sorting
        if sort:
            sort_field = sort[1:] if sort.startswith("-") else sort
            sort_direction = "desc" if sort.startswith("-") else "asc"
            if hasattr(StatusLog, sort_field):
                query = query.order_by(
                    getattr(getattr(StatusLog, sort_field), sort_direction)()
                )
        else:
            # Default to sorting by timestamp descending
            query = query.order_by(StatusLog.timestamp.desc())

        total = query.count()
        status_logs = query.offset((page - 1) * per_page).limit(per_page).all()

        return status_logs, total

    @staticmethod
    def get_status_logs_grouped(
        *,
        group_by: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        sort: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate status log snapshots by the requested interval.

        Args:
            group_by: Aggregation interval (hour, day, week, month)
            start_date: Optional start timestamp filter
            end_date: Optional end timestamp filter
            sort: Sort direction (prefixed with '-' for descending)

        Returns:
            list of dictionaries containing aggregated status metrics
        """

        valid_groupings = {"hour", "day", "week", "month"}
        if group_by not in valid_groupings:
            raise ValueError(f"Unsupported group_by value: {group_by}")

        bucket = func.date_trunc(group_by, StatusLog.timestamp).label("bucket")

        query = db.session.query(
            bucket,
            func.avg(StatusLog.executions_pending).label("avg_pending"),
            func.avg(StatusLog.executions_ready).label("avg_ready"),
            func.avg(StatusLog.executions_running).label("avg_running"),
            func.sum(StatusLog.executions_finished).label("total_finished"),
            func.sum(StatusLog.executions_failed).label("total_failed"),
            func.sum(StatusLog.executions_cancelled).label("total_cancelled"),
        )

        if start_date:
            query = query.filter(StatusLog.timestamp >= start_date)
        if end_date:
            query = query.filter(StatusLog.timestamp <= end_date)

        query = query.group_by(bucket)

        sort_desc = sort is not None and sort.startswith("-")
        if sort_desc:
            query = query.order_by(bucket.desc())
        else:
            query = query.order_by(bucket.asc())

        rows = query.all()

        temp_results: list[dict[str, Any]] = []
        for row in rows:
            bucket_ts = row.bucket
            if bucket_ts and bucket_ts.tzinfo is None:
                bucket_ts = bucket_ts.replace(tzinfo=UTC)

            pending = int(round(float(row.avg_pending or 0)))
            ready = int(round(float(row.avg_ready or 0)))
            running = int(round(float(row.avg_running or 0)))
            finished_total = int(round(float(row.total_finished or 0)))
            failed_total = int(round(float(row.total_failed or 0)))
            cancelled_total = int(round(float(row.total_cancelled or 0)))

            entry = {
                "timestamp": bucket_ts.isoformat() if bucket_ts else None,
                "executions_pending": pending,
                "executions_ready": ready,
                "executions_running": running,
                "executions_finished": finished_total,
                "executions_failed": failed_total,
                "executions_cancelled": cancelled_total,
            }
            entry["executions_active"] = pending + ready + running
            entry["_bucket_dt"] = bucket_ts
            entry["_total_finished"] = finished_total
            entry["_total_failed"] = failed_total
            entry["_total_cancelled"] = cancelled_total

            temp_results.append(entry)

        if temp_results:
            cumulative_totals: dict[str, int] = {
                "_total_finished": 0,
                "_total_failed": 0,
                "_total_cancelled": 0,
            }

            sorted_entries = sorted(
                temp_results,
                key=lambda item: item.get("_bucket_dt")
                or datetime.min.replace(tzinfo=UTC),
            )

            cumulative_key_map = {
                "_total_finished": "cumulative_finished",
                "_total_failed": "cumulative_failed",
                "_total_cancelled": "cumulative_cancelled",
            }

            for item in sorted_entries:
                for temp_key, cumulative_key in cumulative_key_map.items():
                    cumulative_totals[temp_key] += item.get(temp_key, 0)
                    item[cumulative_key] = cumulative_totals[temp_key]

        results: list[dict[str, Any]] = []
        for entry in temp_results:
            entry.pop("_bucket_dt", None)
            entry.pop("_total_finished", None)
            entry.pop("_total_failed", None)
            entry.pop("_total_cancelled", None)
            results.append(entry)

        return results
