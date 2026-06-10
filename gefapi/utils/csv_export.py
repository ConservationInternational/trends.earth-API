"""Shared CSV export utilities for admin data exports."""

import csv
from datetime import datetime
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _parse_date_param(value: str | None) -> datetime | None:
    """Parse an ISO 8601 date string into a datetime object.

    Accepts both date-only (``YYYY-MM-DD``) and full ISO 8601 strings.
    Returns ``None`` when *value* is falsy or cannot be parsed.
    """
    if not value:
        return None
    try:
        import dateutil.parser

        return dateutil.parser.parse(value)
    except Exception:
        logger.warning("Could not parse date param: %r", value)
        return None


def rows_to_csv_response(rows: list[dict[str, Any]], filename: str):
    """Convert a list of flat dicts into a streaming CSV ``Flask.Response``.

    Args:
        rows: List of dictionaries; every dict must share the same keys.
        filename: Suggested download filename (e.g. ``users_export.csv``).

    Returns:
        A Flask ``Response`` with ``Content-Type: text/csv``.
    """
    from flask import Response

    if not rows:
        # Return an empty CSV with no headers when there is no data.
        return Response(
            "",
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Cache-Control": "no-store",
            },
        )

    output = io.StringIO()
    writer = csv.DictWriter(
        output, fieldnames=list(rows[0].keys()), extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-store",
        },
    )
