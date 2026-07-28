"""Utils module"""

import datetime


def utcnow() -> datetime.datetime:
    """Return the current UTC time as a timezone-naive UTC datetime.

    Uses ``datetime.now(UTC)`` rather than the deprecated ``utcnow()``, while
    keeping the result naive for compatibility with the existing DB schema
    which uses ``TIMESTAMP WITHOUT TIME ZONE`` columns.
    """
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def mask_email(email: str) -> str:
    """Mask an email address for safe inclusion in log messages.

    Examples:
        ``mask_email("alex@example.com")``  →  ``"al***@exa***.com"``
    """
    if not email or not isinstance(email, str) or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    local_masked = local[:2] + "***" if len(local) > 2 else local[0] + "***"
    parts = domain.rsplit(".", 1)
    if len(parts) == 2:
        dom, tld = parts
        dom_masked = dom[:3] + "***" if len(dom) > 3 else dom[0] + "***"
        domain_masked = f"{dom_masked}.{tld}"
    else:
        domain_masked = domain[:3] + "***"
    return f"{local_masked}@{domain_masked}"
