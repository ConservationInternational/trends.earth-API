"""Shared utilities for parsing filter and sort query parameters.

This module centralises the logic for translating the SQL-style ``filter``
and ``sort`` query strings sent by the UI into SQLAlchemy filter/order
clauses.  It is used by the execution, user, and script services.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import and_, asc, desc, func, or_

logger = logging.getLogger(__name__)

# Pre-compiled patterns -------------------------------------------------

# Matches a parenthesised OR group, e.g.
#   (status='PENDING' OR status='RUNNING')
_OR_GROUP_RE = re.compile(
    r"^\(\s*(.+?)\s*\)$",
    re.IGNORECASE,
)

# Matches a single comparison expression, e.g.
#   field='value'   field like '%text%'   field>=42
_SIMPLE_EXPR_RE = re.compile(
    r"(\w+)\s*(=|!=|>=|<=|>|<| like )\s*(.+)",
    re.IGNORECASE,
)


# Helper -------------------------------------------------------------------


def _is_string_column(field: str, col: Any, string_field_names: set[str] | None = None) -> bool:
    """Return True when *col* should be compared case-insensitively."""
    if string_field_names and field in string_field_names:
        return True
    try:
        if (
            hasattr(col.type, "python_type")
            and isinstance(col.type.python_type, type)
            and issubclass(col.type.python_type, str)
        ):
            return True
    except NotImplementedError:
        pass
    type_str = str(getattr(col, "type", "")).upper()
    return type_str.startswith(("VARCHAR", "TEXT", "STRING"))


def _build_comparison(col: Any, op: str, value: str, *, is_string: bool) -> Any:
    """Build a single SQLAlchemy comparison clause."""
    if op == "=":
        return func.lower(col) == value.lower() if is_string else col == value
    if op == "!=":
        return func.lower(col) != value.lower() if is_string else col != value
    if op == ">":
        return col > value
    if op == "<":
        return col < value
    if op == ">=":
        return col >= value
    if op == "<=":
        return col <= value
    if op == "like":
        return col.ilike(value)
    return None


# Public API ---------------------------------------------------------------


def parse_single_expression(
    expr: str,
    *,
    allowed_fields: set[str],
    resolve_column: Any,
    string_field_names: set[str] | None = None,
) -> Any | None:
    """Parse one ``field op value`` expression and return a SQLAlchemy clause.

    Parameters
    ----------
    expr:
        A single expression such as ``status='PENDING'`` or
        ``script_name like '%test%'``.
    allowed_fields:
        Set of lowercase field names that are permitted.
    resolve_column:
        Callable ``(field_name: str) -> column | None`` that maps a field
        name to the appropriate SQLAlchemy column (performing JOINs as
        needed).
    string_field_names:
        Optional set of field names that should always be treated as
        string columns for case-insensitive comparison.

    Returns
    -------
    A SQLAlchemy filter clause, or *None* if the expression was invalid or
    the field is not allowed.
    """
    m = _SIMPLE_EXPR_RE.match(expr.strip())
    if not m:
        return None

    field, op, value = m.groups()
    field = field.strip().lower()
    op = op.strip().lower()
    value = value.strip().strip("'\"")

    if field not in allowed_fields:
        logger.warning("[QUERY_FILTERS]: Rejected filter on disallowed field: %s", field)
        return None

    col = resolve_column(field)
    if col is None:
        return None

    is_string = _is_string_column(field, col, string_field_names)
    return _build_comparison(col, op, value, is_string=is_string)


def parse_filter_param(
    filter_param: str,
    *,
    allowed_fields: set[str],
    resolve_column: Any,
    string_field_names: set[str] | None = None,
) -> list[Any]:
    """Parse a complete ``filter`` query-string value.

    Supports:
    * Simple comma-separated expressions: ``status='RUNNING',script_name like '%foo%'``
    * Parenthesised OR groups: ``(status='PENDING' OR status='RUNNING')``
    * Mixed: ``(status='PENDING' OR status='RUNNING'),script_name like '%foo%'``

    Returns a list of SQLAlchemy clauses suitable for
    ``query.filter(and_(*clauses))``.
    """
    clauses: list[Any] = []

    for raw_expr in _split_filter_expressions(filter_param):
        raw_expr = raw_expr.strip()
        if not raw_expr:
            continue

        or_match = _OR_GROUP_RE.match(raw_expr)
        if or_match:
            # Parenthesised OR group — split inner content on " OR "
            inner = or_match.group(1)
            or_clauses: list[Any] = []
            for sub_expr in re.split(r"\s+OR\s+", inner, flags=re.IGNORECASE):
                clause = parse_single_expression(
                    sub_expr,
                    allowed_fields=allowed_fields,
                    resolve_column=resolve_column,
                    string_field_names=string_field_names,
                )
                if clause is not None:
                    or_clauses.append(clause)
            if or_clauses:
                clauses.append(or_(*or_clauses))
        else:
            clause = parse_single_expression(
                raw_expr,
                allowed_fields=allowed_fields,
                resolve_column=resolve_column,
                string_field_names=string_field_names,
            )
            if clause is not None:
                clauses.append(clause)

    return clauses


def _split_filter_expressions(filter_param: str) -> list[str]:
    """Split a filter string on commas, respecting parentheses.

    ``(status='A' OR status='B'),name like '%x%'`` →
    ``["(status='A' OR status='B')", "name like '%x%'"]``
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in filter_param:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def parse_sort_param(
    sort_param: str,
    *,
    allowed_fields: set[str],
    resolve_column: Any,
) -> list[Any]:
    """Parse a ``sort`` query-string value into SQLAlchemy order clauses.

    Format: ``"field direction[,field direction]"`` where *direction* is
    ``asc`` or ``desc`` (default ``asc``).

    Parameters
    ----------
    resolve_column:
        Callable ``(field_name: str, direction: str) -> column | None``
        that may return a pre-ordered expression (for computed fields like
        ``duration``).  When it returns a raw column the caller is
        responsible for applying the direction, but this function will do
        it.

    Returns a list of order-by clauses.
    """
    order_clauses: list[Any] = []

    for sort_expr in sort_param.split(","):
        sort_expr = sort_expr.strip()
        if not sort_expr:
            continue
        parts = sort_expr.split()
        field = parts[0].lower()
        direction = parts[1].lower() if len(parts) > 1 else "asc"

        if field not in allowed_fields:
            logger.warning("[QUERY_FILTERS]: Rejected sort on disallowed field: %s", field)
            continue

        col_or_ordered = resolve_column(field, direction)
        if col_or_ordered is None:
            continue

        # If resolve_column already returned an ordered expression, use it
        # directly (signalled by returning a tuple ``(expr, True)``).
        if isinstance(col_or_ordered, tuple):
            order_clauses.append(col_or_ordered[0])
        else:
            order_clauses.append(desc(col_or_ordered) if direction == "desc" else asc(col_or_ordered))

    return order_clauses
