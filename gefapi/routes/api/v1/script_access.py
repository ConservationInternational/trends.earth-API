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
    """
    Retrieve script access control configuration.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: View current access control settings for script management

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": true,
        "allowed_roles": ["ADMIN", "SUPERADMIN"],
        "allowed_users": ["user123", "user456"],
        "access_type": "role_and_user_restricted"
      }
    }
    ```

    **Response Fields**:
    - `script_id`: The script identifier
    - `restricted`: Whether script has any access restrictions
    - `allowed_roles`: List of roles with access to this script
    - `allowed_users`: List of user IDs with explicit access
    - `access_type`: Type of restriction applied

    **Access Types**:
    - `unrestricted`: No access controls - available to all authenticated users
    - `role_restricted`: Only specific roles can access
    - `user_restricted`: Only specific users can access
    - `role_and_user_restricted`: Both role and user restrictions apply

    **Use Cases**:
    - Review script permissions before modification
    - Audit script access controls for compliance
    - Troubleshoot user access issues
    - Script security management

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to retrieve access controls
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
    """
    Set role-based access control for a script.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Configure which user roles can access and execute a script

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID

    **Request Schema**:
    ```json
    {
      "roles": ["ADMIN", "SUPERADMIN"]
    }
    ```

    **Request Fields**:
    - `roles`: Array of role names that should have access
      (empty array removes restrictions)

    **Valid Roles**:
    - `USER`: Regular users
    - `ADMIN`: Administrator users
    - `SUPERADMIN`: Super administrator users

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": true,
        "allowed_roles": ["ADMIN", "SUPERADMIN"],
        "allowed_users": [],
        "access_type": "role_restricted"
      }
    }
    ```

    **Behavior**:
    - Empty roles array removes all role restrictions
    - Non-empty array restricts access to specified roles only
    - Users with specified roles can access script regardless of user-specific
      restrictions
    - Higher privilege roles (SUPERADMIN) can access scripts restricted to
      lower roles

    **Use Cases**:
    - Restrict sensitive analysis scripts to admin users only
    - Make scripts available to all authenticated users
    - Create role-based script catalogs
    - Implement script security policies

    **Error Responses**:
    - `400 Bad Request`: Missing 'roles' field or invalid role names
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to update access controls
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
    """
    Set user-specific access control for a script.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Configure which specific users can access and execute a script

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID

    **Request Schema**:
    ```json
    {
      "users": ["user123", "user456"]
    }
    ```

    **Request Fields**:
    - `users`: Array of user IDs that should have access
      (empty array removes restrictions)

    **User Validation**:
    - All provided user IDs must exist in the system
    - User IDs can be numeric IDs or email addresses
    - Invalid user IDs will cause the request to fail

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": true,
        "allowed_roles": [],
        "allowed_users": ["user123", "user456"],
        "access_type": "user_restricted"
      }
    }
    ```

    **Behavior**:
    - Empty users array removes all user-specific restrictions
    - Non-empty array restricts access to specified users only
    - User restrictions work independently of role restrictions
    - Users in the allowed list can access script regardless of their role

    **Access Logic**:
    - If both role and user restrictions exist: user must match either criteria
    - User-specific access overrides role-based restrictions
    - Admins can always access scripts regardless of restrictions

    **Use Cases**:
    - Grant script access to specific researchers or partners
    - Create private scripts for limited user groups
    - Implement per-project script access controls
    - Beta testing with selected users

    **Error Responses**:
    - `400 Bad Request`: Missing 'users' field, invalid user IDs, or user not found
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to update access controls
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
    """
    Add a specific user to script access control list.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Grant script access to an individual user

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID
    - `user_id`: User identifier (numeric ID or email address)

    **Request**: No request body required

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": true,
        "allowed_roles": [],
        "allowed_users": ["user123", "user456", "user789"],
        "access_type": "user_restricted"
      }
    }
    ```

    **Behavior**:
    - Adds user to existing user access list (if any)
    - Creates user restriction if none existed
    - User is validated to exist in system before adding
    - Duplicate additions are handled gracefully (no error)

    **Access Effect**:
    - User gains immediate access to script
    - User can discover script in their script listings
    - User can execute script regardless of their role
    - Access persists until explicitly removed

    **Use Cases**:
    - Grant access to individual researchers
    - Add collaborators to private scripts
    - Provide temporary access for specific projects
    - Incrementally build user access lists

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script or user does not exist
    - `500 Internal Server Error`: Failed to add user access
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
    """
    Remove a specific user from script access control list.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Revoke script access from an individual user

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID
    - `user_id`: User identifier (numeric ID or email address)

    **Request**: No request body required

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": true,
        "allowed_roles": [],
        "allowed_users": ["user123"],
        "access_type": "user_restricted"
      }
    }
    ```

    **Behavior**:
    - Removes user from script's allowed users list
    - User immediately loses access to script
    - Does not affect other users' access
    - Gracefully handles removal of non-existent users

    **Access Effect**:
    - User can no longer see script in listings
    - User cannot execute script
    - Existing executions continue to completion
    - User may regain access through role-based permissions

    **Cleanup Logic**:
    - If user list becomes empty, user restrictions may be removed
    - Script may become unrestricted if no other access controls exist
    - Access type is recalculated based on remaining restrictions

    **Use Cases**:
    - Remove access when user leaves project
    - Revoke access due to security concerns
    - Clean up outdated user permissions
    - Manage temporary access grants

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to remove user access
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
    """
    Add a specific role to script access control list.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Grant script access to all users with a specific role

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID
    - `role`: Role name to add (USER, ADMIN, or SUPERADMIN)

    **Request**: No request body required

    **Valid Roles**:
    - `USER`: Regular users
    - `ADMIN`: Administrator users
    - `SUPERADMIN`: Super administrator users

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": true,
        "allowed_roles": ["ADMIN", "SUPERADMIN"],
        "allowed_users": [],
        "access_type": "role_restricted"
      }
    }
    ```

    **Behavior**:
    - Adds role to existing role access list (if any)
    - Creates role restriction if none existed
    - All users with the specified role gain access
    - Duplicate additions are handled gracefully (no error)

    **Access Effect**:
    - All current and future users with this role can access script
    - Role-based access is dynamic (new users with role get access automatically)
    - Higher privilege roles typically include lower privilege access
    - Access persists until role is explicitly removed

    **Use Cases**:
    - Grant access to all admin users for management scripts
    - Make scripts available to all regular users
    - Implement role-based script catalogs
    - Create tiered access based on user privileges

    **Error Responses**:
    - `400 Bad Request`: Invalid role name
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to add role access
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
    """
    Remove a specific role from script access control list.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Revoke script access from all users with a specific role

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID
    - `role`: Role name to remove (USER, ADMIN, or SUPERADMIN)

    **Request**: No request body required

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": true,
        "allowed_roles": ["ADMIN"],
        "allowed_users": [],
        "access_type": "role_restricted"
      }
    }
    ```

    **Behavior**:
    - Removes role from script's allowed roles list
    - All users with only this role lose access immediately
    - Does not affect other roles' access
    - Gracefully handles removal of non-existent roles

    **Access Effect**:
    - Users with this role can no longer see script in listings
    - Users with this role cannot execute script
    - Existing executions continue to completion
    - Users may retain access through user-specific permissions or other roles

    **Cleanup Logic**:
    - If role list becomes empty, role restrictions may be removed
    - Script may become unrestricted if no other access controls exist
    - Access type is recalculated based on remaining restrictions

    **Use Cases**:
    - Remove access when role permissions change
    - Tighten security by restricting to higher privilege roles only
    - Clean up outdated role-based permissions
    - Implement policy changes across scripts

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to remove role access
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
    """
    Remove all access restrictions from a script.

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Make script accessible to all authenticated users

    **Path Parameters**:
    - `script_id`: Script identifier/slug or numeric ID

    **Request**: No request body required

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "12345",
        "restricted": false,
        "allowed_roles": [],
        "allowed_users": [],
        "access_type": "unrestricted"
      }
    }
    ```

    **Clearing Process**:
    - Removes all role-based restrictions
    - Removes all user-specific restrictions
    - Makes script available to all authenticated users
    - Updates script access type to "unrestricted"

    **Effect**:
    - Any authenticated user can discover and execute the script
    - Script appears in public script listings
    - No special permissions required for access
    - Maintains script visibility in search results

    **Use Cases**:
    - Making private scripts public
    - Removing outdated access restrictions
    - Simplifying script access management
    - Opening scripts for general use

    **Security Considerations**:
    - Ensure script content is appropriate for public access
    - Review script parameters for sensitive data exposure
    - Consider impact on system resources from increased usage
    - Document decision for audit purposes

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to clear access restrictions
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
