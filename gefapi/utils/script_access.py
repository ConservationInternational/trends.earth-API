"""Script access control utilities"""

import json

from gefapi.utils.permissions import is_admin_or_higher


def can_manage_script_access(user, script):
    """Check if user can manage access control for a script

    Only ADMIN and SUPERADMIN users can modify script access controls.
    Script owners cannot modify access controls unless they are admin users.
    """
    return is_admin_or_higher(user)


def set_script_roles(script, roles):
    """Set allowed roles for a script"""
    if roles:
        script.allowed_roles = json.dumps(roles) if isinstance(roles, list) else roles
        script.restricted = True
    else:
        script.allowed_roles = None
    return script


def set_script_users(script, user_ids):
    """Set allowed users for a script"""
    if user_ids:
        if isinstance(user_ids, list):
            script.allowed_users = json.dumps(user_ids)
        else:
            script.allowed_users = user_ids
        script.restricted = True
    else:
        script.allowed_users = None
    return script


def add_user_to_script(script, user_id):
    """Add a user to the allowed users list"""
    allowed_users = script.get_allowed_users()
    if str(user_id) not in allowed_users:
        allowed_users.append(str(user_id))
        set_script_users(script, allowed_users)
    return script


def remove_user_from_script(script, user_id):
    """Remove a user from the allowed users list"""
    allowed_users = script.get_allowed_users()
    if str(user_id) in allowed_users:
        allowed_users.remove(str(user_id))
        set_script_users(script, allowed_users if allowed_users else None)
    return script


def add_role_to_script(script, role):
    """Add a role to the allowed roles list"""
    allowed_roles = script.get_allowed_roles()
    if role not in allowed_roles:
        allowed_roles.append(role)
        set_script_roles(script, allowed_roles)
    return script


def remove_role_from_script(script, role):
    """Remove a role from the allowed roles list"""
    allowed_roles = script.get_allowed_roles()
    if role in allowed_roles:
        allowed_roles.remove(role)
        set_script_roles(script, allowed_roles if allowed_roles else None)
    return script


def clear_script_restrictions(script):
    """Remove all access restrictions from a script"""
    script.allowed_roles = None
    script.allowed_users = None
    script.restricted = False
    return script


def get_access_summary(script):
    """Get a summary of script access controls"""
    summary = {
        "restricted": script.restricted,
        "public": script.public,
        "allowed_roles": script.get_allowed_roles(),
        "allowed_users": script.get_allowed_users(),
    }

    if not script.restricted and not script.public:
        summary["access_type"] = "owner_only"
    elif script.public and not script.restricted:
        summary["access_type"] = "public"
    elif script.restricted:
        summary["access_type"] = "restricted"
    else:
        summary["access_type"] = "owner_only"

    return summary
