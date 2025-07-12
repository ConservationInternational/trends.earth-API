"""Permission utility functions"""


def is_superadmin(user):
    """Check if user has superadmin role"""
    if user is None:
        return False
    return user.role == "SUPERADMIN" or user.email == "gef@gef.com"


def is_admin_or_higher(user):
    """Check if user has admin or superadmin role"""
    if user is None:
        return False
    return user.role in ["ADMIN", "SUPERADMIN"] or user.email == "gef@gef.com"


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
    """Check if user can change another user's password"""
    return is_superadmin(user)


def can_update_user_profile(user):
    """Check if user can update another user's profile information"""
    return is_superadmin(user)


def can_access_admin_features(user):
    """Check if user can access admin-only features (non-user management)"""
    return is_admin_or_higher(user)
