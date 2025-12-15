"""Permission utility functions"""

from __future__ import annotations

from gefapi.config import SETTINGS


def _configured_admin_email() -> str | None:
    """Return the configured API environment user email if available."""

    configured_email = SETTINGS.get("API_ENVIRONMENT_USER")
    if isinstance(configured_email, str):
        configured_email = configured_email.strip().lower()
        return configured_email or None
    return None


def _is_configured_admin_email(user) -> bool:
    email = getattr(user, "email", None)
    configured_email = _configured_admin_email()
    if not email or not configured_email:
        return False
    return email.lower() == configured_email


def is_protected_admin_email(email: str | None) -> bool:
    """Return True if the provided email is protected from destructive actions."""

    if not email:
        return False
    configured_email = _configured_admin_email()
    return bool(configured_email and email.lower() == configured_email)


def is_superadmin(user):
    """Check if user has superadmin role or configured admin email."""
    if user is None:
        return False
    return user.role == "SUPERADMIN" or _is_configured_admin_email(user)


def is_admin_or_higher(user):
    """Check if user has admin or superadmin role or configured email."""
    if user is None:
        return False
    return user.role in ["ADMIN", "SUPERADMIN"] or _is_configured_admin_email(user)


def can_manage_users(user):
    """Check if user can manage other users (change roles, delete, etc.)"""
    return is_superadmin(user)


def can_change_user_role(user):
    """Check if user can change another user's role"""
    return is_superadmin(user)


def can_delete_user(user):
    """Check if user can delete another user"""
    return is_superadmin(user)


def can_change_user_password(user):
    """Check if user can change another user's password.

    SUPERADMIN can change any user's password.
    ADMIN can change passwords, but must be prevented from changing SUPERADMIN
    passwords (this is checked at the route level with can_admin_change_user_password).
    """
    return is_admin_or_higher(user)


def can_admin_change_user_password(admin_user, target_user):
    """Check if admin can change a specific user's password.

    Rules:
    - SUPERADMIN can change any user's password
    - ADMIN can change any user's password EXCEPT SUPERADMIN's
    - Non-admins cannot change other users' passwords

    Args:
        admin_user: The admin attempting the password change
        target_user: The user whose password is being changed

    Returns:
        bool: True if the password change is allowed
    """
    if admin_user is None or target_user is None:
        return False

    # SUPERADMINs can change anyone's password
    if is_superadmin(admin_user):
        return True

    # ADMINs can change passwords, but not for SUPERADMINs
    if is_admin_or_higher(admin_user):
        # Check if target is a SUPERADMIN
        return target_user.role != "SUPERADMIN"

    return False


def can_update_user_profile(user):
    """Check if user can update another user's profile information"""
    return is_superadmin(user)


def can_access_admin_features(user):
    """Check if user can access admin-only features (non-user management)"""
    return is_admin_or_higher(user)
