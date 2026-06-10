"""Shared CSV export utilities for admin data exports."""

import csv
from datetime import datetime
import io
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Characters that trigger formula execution in spreadsheet applications when
# they appear at the start of a cell value (CWE-1236 / OWASP CSV injection).
_FORMULA_TRIGGER_RE = re.compile(r"^[=+\-@\t\r]")

# Maximum number of rows returned by any export endpoint to prevent memory
# exhaustion through unbounded queries.
MAX_EXPORT_ROWS = 100_000


def _sanitize_csv_cell(value: Any) -> Any:
    """Prefix string cells that start with formula-triggering characters.

    Spreadsheet applications (Excel, LibreOffice Calc) interpret cells that
    start with ``=``, ``+``, ``-``, ``@``, ``\\t``, or ``\\r`` as formulas.
    An attacker who controls their profile data could craft a cell value that
    exfiltrates data or executes commands when opened by an admin.

    The mitigation is to prefix the offending character with a tab so the
    application treats the cell as plain text.  The original value is
    preserved (just tab-prefixed) so the data remains legible.
    """
    if isinstance(value, str) and _FORMULA_TRIGGER_RE.match(value):
        return "\t" + value
    return value


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *row* with all string cell values sanitized."""
    return {k: _sanitize_csv_cell(v) for k, v in row.items()}


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

    Each string cell is sanitized against CSV/formula injection (CWE-1236)
    before writing.

    Args:
        rows: List of dictionaries; every dict must share the same keys.
        filename: Suggested download filename (e.g. ``users_export.csv``).
            Must not contain ``"`` or newline characters.

    Returns:
        A Flask ``Response`` with ``Content-Type: text/csv``.
    """
    from flask import Response

    # Strip characters that could break the Content-Disposition header value.
    safe_filename = re.sub(r'["\r\n]', "_", filename)

    if not rows:
        return Response(
            "",
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Cache-Control": "no-store",
            },
        )

    sanitized = [_sanitize_row(row) for row in rows]

    output = io.StringIO()
    writer = csv.DictWriter(
        output, fieldnames=list(sanitized[0].keys()), extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(sanitized)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Cache-Control": "no-store",
        },
    )
