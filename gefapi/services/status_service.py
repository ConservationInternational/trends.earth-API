"""STATUS SERVICE"""

import logging

from gefapi import db
from gefapi.models import StatusLog

logger = logging.getLogger()


class StatusService:
    """Status Service Class"""

    @staticmethod
    def get_status_logs(
        start_date=None,
        end_date=None,
        sort=None,
        page=1,
        per_page=100,
    ):
        """Get status logs with optional filtering and pagination"""
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
