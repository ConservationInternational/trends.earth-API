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
    Retrieve script access control information and permissions.

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: View current access control settings for script management

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier

    **Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": true,
        "allowed_roles": ["ADMIN", "SUPERADMIN"],
        "allowed_users": ["user-456", "user-789"],
        "access_type": "role_and_user_restricted",
        "total_restrictions": 4
      }
    }
    ```

    **Access Control Types**:
    - `unrestricted`: Script available to all authenticated users
    - `role_restricted`: Only specific roles can access
    - `user_restricted`: Only specific users can access
    - `role_and_user_restricted`: Both role and user restrictions apply

    **Restriction Information**:
    - `restricted`: Boolean indicating if any restrictions exist
    - `allowed_roles`: Array of roles with access (USER, ADMIN, SUPERADMIN)
    - `allowed_users`: Array of user IDs with explicit access
    - `access_type`: Summary of restriction type
    - `total_restrictions`: Count of total access rules

    **Access Logic**:
    - If no restrictions: All authenticated users can access
    - If role restrictions only: Users with matching roles can access
    - If user restrictions only: Explicitly listed users can access
    - If both restrictions: Users must match either role OR be explicitly listed

    **Use Cases**:
    - Audit script access permissions
    - Review security settings before publishing
    - Compliance and access control reporting
    - Troubleshooting access issues

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to retrieve access information
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
    Set or update allowed roles for script access control.

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: Configure role-based access restrictions for script execution

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier

    **Request Schema**:
    ```json
    {
      "roles": ["ADMIN", "SUPERADMIN"]
    }
    ```

    **Request Fields**:
    - `roles`: Array of roles allowed to access the script
      - Valid roles: `USER`, `ADMIN`, `SUPERADMIN`
      - Empty array removes all role restrictions
      - Must be an array (can be empty)

    **Success Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": true,
        "allowed_roles": ["ADMIN", "SUPERADMIN"],
        "allowed_users": [],
        "access_type": "role_restricted",
        "total_restrictions": 2
      }
    }
    ```

    **Role-Based Access Control**:
    - `USER`: Regular users with basic permissions
    - `ADMIN`: Administrative users with elevated permissions
    - `SUPERADMIN`: Super administrators with full system access

    **Update Behavior**:
    - Replaces existing role restrictions completely
    - Empty array removes all role restrictions
    - Does not affect user-specific restrictions
    - Changes take effect immediately

    **Access Logic After Update**:
    - If roles specified: Only users with matching roles can access
    - If roles empty: Role restrictions removed (user restrictions may still apply)
    - Combined with user restrictions: Users need matching role OR explicit user access

    **Use Cases**:
    - Restrict sensitive scripts to admin users only
    - Create role-based script categories
    - Implement organizational access policies
    - Security compliance requirements

    **Error Responses**:
    - `400 Bad Request`: Invalid roles, missing field, or malformed request
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to update role restrictions
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
    Set or update allowed users for script access control.

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: Configure user-specific access restrictions for script execution

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier

    **Request Schema**:
    ```json
    {
      "users": ["user-123", "user-456"]
    }
    ```

    **Request Fields**:
    - `users`: Array of user IDs allowed to access the script
      - Must be valid existing user IDs
      - Empty array removes all user restrictions
      - Must be an array (can be empty)

    **Success Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": true,
        "allowed_roles": [],
        "allowed_users": ["user-123", "user-456"],
        "access_type": "user_restricted",
        "total_restrictions": 2
      }
    }
    ```

    **User-Specific Access Control**:
    - Grants explicit access to individual users
    - Bypasses role-based restrictions for listed users
    - Users must exist in the system
    - User IDs are validated before applying restrictions

    **Update Behavior**:
    - Replaces existing user restrictions completely
    - Empty array removes all user restrictions
    - Does not affect role-based restrictions
    - Changes take effect immediately
    - Validates all user IDs exist before updating

    **Access Logic After Update**:
    - If users specified: Only explicitly listed users can access
    - If users empty: User restrictions removed (role restrictions may still apply)
    - Combined with role restrictions: Users need matching role OR explicit user access

    **User ID Validation**:
    - All provided user IDs must exist in the system
    - Invalid user IDs cause the entire request to fail
    - No partial updates - all or nothing approach

    **Use Cases**:
    - Grant access to specific collaborators
    - Create exclusive user groups for sensitive scripts
    - Temporary access for project teams
    - Fine-grained access control management

    **Error Responses**:
    - `400 Bad Request`: Invalid user IDs, missing field, or malformed request
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin privileges required
    - `404 Not Found`: Script does not exist or invalid user ID
    - `500 Internal Server Error`: Failed to update user restrictions
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
    Add a specific user to script access permissions.

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: Grant script access to an individual user

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier
    - `user_id`: User ID to grant access to

    **Request**: No request body required - this is a POST endpoint

    **Success Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": true,
        "allowed_roles": [],
        "allowed_users": ["user-123", "user-456", "user-789"],
        "access_type": "user_restricted",
        "total_restrictions": 3
      }
    }
    ```

    **Add User Process**:
    1. Validates script exists and user has admin permissions
    2. Validates target user exists in the system
    3. Adds user to the script's allowed users list
    4. Returns updated access control information
    5. Changes take effect immediately

    **Behavior**:
    - Adds user to existing access list (does not replace)
    - No effect if user already has access
    - Does not affect role-based restrictions
    - User gains immediate access to the script

    **Access Grant Logic**:
    - User can now execute the script regardless of role
    - Overrides role restrictions for this specific user
    - Combined with existing role and user restrictions
    - Access persists until explicitly removed

    **User Validation**:
    - User ID must exist in the system
    - User account must be active
    - Validates user before granting access

    **Use Cases**:
    - Grant temporary access to collaborators
    - Add team members to project scripts
    - Exception handling for role-based restrictions
    - Individual user permission management

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
    Remove a specific user from script access permissions.

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: Revoke script access from an individual user

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier
    - `user_id`: User ID to revoke access from

    **Request**: No request body required - this is a DELETE endpoint

    **Success Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": true,
        "allowed_roles": [],
        "allowed_users": ["user-123"],
        "access_type": "user_restricted",
        "total_restrictions": 1
      }
    }
    ```

    **Remove User Process**:
    1. Validates script exists and user has admin permissions
    2. Removes user from the script's allowed users list
    3. Returns updated access control information
    4. Changes take effect immediately
    5. Does not validate if user exists (allows cleanup)

    **Behavior**:
    - Removes user from existing access list
    - No error if user was not in the list
    - Does not affect role-based restrictions
    - User loses explicit access immediately

    **Access Revocation Logic**:
    - User can no longer execute script via user access
    - May still have access via role-based permissions
    - Only removes explicit user permission
    - Does not affect other users' permissions

    **Cleanup Behavior**:
    - Works even if user account no longer exists
    - Useful for cleaning up permissions after user deletion
    - Safe operation that cannot harm access controls

    **Use Cases**:
    - Remove temporary access from collaborators
    - Clean up permissions after project completion
    - Revoke access for security reasons
    - User account lifecycle management

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
    Add a specific role to script access permissions.

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: Grant script access to all users with a specific role

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier
    - `role`: Role to grant access to (USER, ADMIN, SUPERADMIN)

    **Request**: No request body required - this is a POST endpoint

    **Success Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": true,
        "allowed_roles": ["ADMIN", "SUPERADMIN"],
        "allowed_users": [],
        "access_type": "role_restricted",
        "total_restrictions": 2
      }
    }
    ```

    **Valid Roles**:
    - `USER`: Regular users with basic permissions
    - `ADMIN`: Administrative users with elevated permissions
    - `SUPERADMIN`: Super administrators with full system access

    **Add Role Process**:
    1. Validates script exists and user has admin permissions
    2. Validates role is valid (USER, ADMIN, SUPERADMIN)
    3. Adds role to the script's allowed roles list
    4. Returns updated access control information
    5. Changes take effect immediately

    **Behavior**:
    - Adds role to existing access list (does not replace)
    - No effect if role already has access
    - Does not affect user-specific restrictions
    - All users with this role gain immediate access

    **Access Grant Logic**:
    - All users with the specified role can execute the script
    - Combined with existing role and user restrictions
    - Role permissions apply to current and future users
    - Access persists until role is explicitly removed

    **Use Cases**:
    - Grant access to all administrators
    - Create role-based script categories
    - Implement organizational access policies
    - Bulk permission management

    **Error Responses**:
    - `400 Bad Request`: Invalid role specified
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
    Remove a specific role from script access permissions.

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: Revoke script access from all users with a specific role

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier
    - `role`: Role to revoke access from (USER, ADMIN, SUPERADMIN)

    **Request**: No request body required - this is a DELETE endpoint

    **Success Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": true,
        "allowed_roles": ["ADMIN"],
        "allowed_users": [],
        "access_type": "role_restricted",
        "total_restrictions": 1
      }
    }
    ```

    **Remove Role Process**:
    1. Validates script exists and user has admin permissions
    2. Removes role from the script's allowed roles list
    3. Returns updated access control information
    4. Changes take effect immediately
    5. Does not validate role exists (allows cleanup)

    **Behavior**:
    - Removes role from existing access list
    - No error if role was not in the list
    - Does not affect user-specific restrictions
    - All users with this role lose role-based access

    **Access Revocation Logic**:
    - Users with this role can no longer execute script via role access
    - May still have access via explicit user permissions
    - Only removes role-based permission
    - Does not affect other roles' permissions

    **Impact on Users**:
    - Users lose access unless they have explicit user permission
    - Affects all current and future users with this role
    - Immediate effect - no grace period
    - Users can regain access via individual user grants

    **Use Cases**:
    - Tighten security by removing broad role access
    - Change from role-based to user-specific permissions
    - Respond to security incidents
    - Refine access control policies

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
    Clear all access restrictions from a script (make it publicly accessible).

    **Authentication**: JWT token required
    **Authorization**: Admin or superadmin access required
    **Purpose**: Remove all access controls and make script available to all
    authenticated users

    **Path Parameters**:
    - `script_id`: Script ID or slug identifier

    **Request**: No request body required - this is a DELETE endpoint

    **Success Response Schema**:
    ```json
    {
      "data": {
        "script_id": "script-123",
        "restricted": false,
        "allowed_roles": [],
        "allowed_users": [],
        "access_type": "unrestricted",
        "total_restrictions": 0
      }
    }
    ```

    **Clear Access Process**:
    1. Validates script exists and user has admin permissions
    2. Removes all role-based restrictions
    3. Removes all user-specific restrictions
    4. Sets script to unrestricted access mode
    5. Returns updated access control information

    **Unrestricted Access Effects**:
    - All authenticated users can access and execute the script
    - No role or user restrictions apply
    - Script becomes publicly available within the platform
    - Changes take effect immediately

    **Security Implications**:
    - Script becomes accessible to all users
    - Consider script content and sensitivity before clearing restrictions
    - Audit and compliance considerations
    - Irreversible operation (must re-add restrictions manually)

    **Use Cases**:
    - Make scripts publicly available for general use
    - Remove restrictive access controls
    - Convert private scripts to public utilities
    - Simplify access management

    **Post-Clear Behavior**:
    - Script appears in public script listings
    - All authenticated users can execute
    - No permission checks beyond authentication
    - Can re-add restrictions later if needed

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
