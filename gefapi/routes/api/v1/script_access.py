"""Script access control API routes"""

from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi import db
from gefapi.models import User
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.script_service import ScriptService
from gefapi.utils.permissions import is_admin_or_higher
from gefapi.utils.script_access import (
    add_role_to_script,
    add_user_to_script,
    clear_script_restrictions,
    get_access_summary,
    remove_role_from_script,
    remove_user_from_script,
    set_script_roles,
    set_script_users,
)


@endpoints.route("/script/<script_id>/access", methods=["GET"])
@jwt_required()
def get_script_access(script_id):
    """Get script access control information

    Returns the current access control settings for a script, including
    allowed roles and users, and whether the script has any restrictions.

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": true,
            "allowed_roles": ["ADMIN", "SUPERADMIN"],
            "allowed_users": ["user123", "user456"],
            "access_type": "role_and_user_restricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can view script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script_id>/access/roles", methods=["PUT"])
@jwt_required()
def set_script_access_roles(script_id):
    """Set allowed roles for script access

    Updates the list of roles that are allowed to access this script.
    Setting an empty list removes all role restrictions.

    Request Body:
    {
        "roles": ["ADMIN", "SUPERADMIN"]
    }

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": true,
            "allowed_roles": ["ADMIN", "SUPERADMIN"],
            "allowed_users": [],
            "access_type": "role_restricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can modify script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        data = request.get_json()
        if not data or "roles" not in data:
            return error(status=400, detail="Missing 'roles' in request body")

        roles = data["roles"]
        valid_roles = ["USER", "ADMIN", "SUPERADMIN"]

        if roles and not isinstance(roles, list):
            return error(status=400, detail="Roles must be a list")

        if roles:
            invalid_roles = [r for r in roles if r not in valid_roles]
            if invalid_roles:
                return error(status=400, detail=f"Invalid roles: {invalid_roles}")

        set_script_roles(script, roles)
        db.session.commit()

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script_id>/access/users", methods=["PUT"])
@jwt_required()
def set_script_access_users(script_id):
    """Set allowed users for script access

    Updates the list of users that are allowed to access this script.
    Setting an empty list removes all user restrictions.

    Request Body:
    {
        "users": ["user123", "user456"]
    }

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": true,
            "allowed_roles": [],
            "allowed_users": ["user123", "user456"],
            "access_type": "user_restricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can modify script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        data = request.get_json()
        if not data or "users" not in data:
            return error(status=400, detail="Missing 'users' in request body")

        user_ids = data["users"]

        if user_ids and not isinstance(user_ids, list):
            return error(status=400, detail="Users must be a list of user IDs")

        # Validate user IDs exist
        if user_ids:
            for user_id in user_ids:
                user = User.query.filter_by(id=user_id).first()
                if not user:
                    return error(status=400, detail=f"User '{user_id}' not found")

        set_script_users(script, user_ids)
        db.session.commit()

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script_id>/access/users/<user_id>", methods=["POST"])
@jwt_required()
def add_script_access_user(script_id, user_id):
    """Add a user to script access

    Adds a specific user to the script's allowed users list.

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": true,
            "allowed_roles": [],
            "allowed_users": ["user123", "user456", "user789"],
            "access_type": "user_restricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can modify script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        user = User.query.filter_by(id=user_id).first()
        if not user:
            return error(status=404, detail="User not found")

        add_user_to_script(script, user_id)
        db.session.commit()

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script_id>/access/users/<user_id>", methods=["DELETE"])
@jwt_required()
def remove_script_access_user(script_id, user_id):
    """Remove a user from script access

    Removes a specific user from the script's allowed users list.

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": true,
            "allowed_roles": [],
            "allowed_users": ["user123"],
            "access_type": "user_restricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can modify script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        remove_user_from_script(script, user_id)
        db.session.commit()

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script_id>/access/roles/<role>", methods=["POST"])
@jwt_required()
def add_script_access_role(script_id, role):
    """Add a role to script access

    Adds a specific role to the script's allowed roles list.

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": true,
            "allowed_roles": ["ADMIN", "SUPERADMIN"],
            "allowed_users": [],
            "access_type": "role_restricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can modify script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        valid_roles = ["USER", "ADMIN", "SUPERADMIN"]
        if role not in valid_roles:
            return error(status=400, detail=f"Invalid role: {role}")

        add_role_to_script(script, role)
        db.session.commit()

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script_id>/access/roles/<role>", methods=["DELETE"])
@jwt_required()
def remove_script_access_role(script_id, role):
    """Remove a role from script access

    Removes a specific role from the script's allowed roles list.

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": true,
            "allowed_roles": ["ADMIN"],
            "allowed_users": [],
            "access_type": "role_restricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can modify script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        remove_role_from_script(script, role)
        db.session.commit()

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script_id>/access", methods=["DELETE"])
@jwt_required()
def clear_script_access(script_id):
    """Clear all access restrictions from a script

    Removes all role and user restrictions from a script, making it
    accessible to all authenticated users.

    Example Response:
    {
        "data": {
            "script_id": "12345",
            "restricted": false,
            "allowed_roles": [],
            "allowed_users": [],
            "access_type": "unrestricted"
        }
    }
    """
    try:
        # Only ADMIN and SUPERADMIN users can modify script access controls
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin privileges required")

        script = ScriptService.get_script(script_id, current_user)
        if not script:
            return error(status=404, detail="Script not found")

        clear_script_restrictions(script)
        db.session.commit()

        summary = get_access_summary(script)
        return jsonify(data=summary), 200

    except Exception as e:
        return error(status=500, detail=str(e))
