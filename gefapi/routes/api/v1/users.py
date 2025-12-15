"""User management routes for the Trends.Earth API."""

import logging

from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi import limiter
from gefapi.errors import (
    AuthError,
    EmailError,
    PasswordValidationError,
    UserDuplicated,
    UserNotFound,
)
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services import UserService
from gefapi.utils.permissions import (
    can_admin_change_user_password,
    can_change_user_password,
    can_change_user_role,
    can_delete_user,
    can_update_user_profile,
    is_admin_or_higher,
    is_protected_admin_email,
)
from gefapi.utils.rate_limiting import (
    RateLimitConfig,
    get_admin_aware_key,
    is_rate_limiting_disabled,
)
from gefapi.validators import validate_user_creation, validate_user_update

logger = logging.getLogger()


@endpoints.route("/user", strict_slashes=False, methods=["POST"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_user_creation_limits()) or "10 per hour",
    key_func=get_admin_aware_key,
    exempt_when=is_rate_limiting_disabled,
)
@validate_user_creation
def create_user():
    """
    Create a new user account.

    **Rate Limited**: Subject to user creation rate limits (configurable)
    **Access**: Public endpoint - no authentication required for basic user creation
    **Admin Features**: Creating ADMIN/SUPERADMIN users requires SUPERADMIN
      authentication

    **Request Schema**:
    ```json
    {
      "email": "user@example.com",
      "password": "securePassword123",
      "name": "John Doe",
      "country": "US",
      "institution": "Example Organization",
      "role": "USER"
    }
    ```

    **Request Fields**:
    - `email`: User's email address (required, must be unique)
    - `password`: User's password (required, minimum security requirements apply)
    - `name`: User's full name (required)
    - `country`: Two-letter country code (optional)
    - `institution`: User's organization/institution (optional)
    - `role`: User role - "USER", "ADMIN", or "SUPERADMIN" (default: "USER")

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "123",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER",
        "country": "US",
        "institution": "Example Organization",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z"
      }
    }
    ```

    **Role Creation Rules**:
    - Anyone can create "USER" accounts
    - Only SUPERADMIN users can create "ADMIN" or "SUPERADMIN" accounts
    - Attempting to create privileged roles without permission returns 403 Forbidden

    **Error Responses**:
    - `400 Bad Request`: Email already exists, validation failed, or weak password
    - `403 Forbidden`: Insufficient privileges to create the requested role
    - `429 Too Many Requests`: Rate limit exceeded
    - `500 Internal Server Error`: User creation failed

    **Query Parameters**:
    - `legacy`: If "true" (default), emails the password directly for backwards
      compatibility with the QGIS plugin. If "false", sends a password reset
      link instead (more secure).
    """
    logger.info("[ROUTER]: Creating user")
    body = request.get_json()

    # Check for legacy query parameter (defaults to true for backwards
    # compatibility with QGIS plugin)
    legacy_param = request.args.get("legacy", "true")
    legacy = legacy_param.lower() != "false"

    if request.headers.get("Authorization", None) is not None:

        @jwt_required()
        def identity():
            pass

        identity()
    identity = current_user
    if identity:
        user_role = body.get("role", "USER")
        # Only superadmin can create admin or superadmin users
        if user_role in ["ADMIN", "SUPERADMIN"] and not can_change_user_role(identity):
            return error(status=403, detail="Forbidden")
    else:
        body["role"] = "USER"
    try:
        user = UserService.create_user(body, legacy=legacy)
    except UserDuplicated as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except PasswordValidationError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=422, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_users():
    """
    Retrieve list of users with filtering, sorting, and pagination.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include
    - `exclude`: Comma-separated list of fields to exclude from response
    - `filter`: Filter users by email, name, role, or other attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-created_at')
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "123",
          "email": "user@example.com",
          "name": "John Doe",
          "role": "USER",
          "country": "US",
          "institution": "Example Organization",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T10:30:00Z"
        }
      ]
    }
    ```

    **Response Schema (with pagination)**:
    ```json
    {
      "data": [...],
      "page": 1,
      "per_page": 20,
      "total": 150
    }
    ```

    **Filtering Examples**:
    - `?filter=admin` - Find users with "admin" in email, name, or role
    - `?filter=role:ADMIN` - Find users with ADMIN role
    - `?filter=country:US` - Find users from United States

    **Sorting Examples**:
    - `?sort=name` - Sort by name ascending
    - `?sort=-created_at` - Sort by creation date descending
    - `?sort=email` - Sort by email ascending

    **Field Control Examples**:
    - `?include=password_last_changed` - Include additional fields
    - `?exclude=institution,country` - Exclude specified fields

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient privileges (ADMIN+ required)
    - `500 Internal Server Error`: Failed to retrieve users
    """
    logger.info("[ROUTER]: Getting all users")

    identity = current_user
    if not is_admin_or_higher(identity):
        return error(status=403, detail="Forbidden")

    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    sort = request.args.get("sort", None)

    page_param = request.args.get("page", None)
    per_page_param = request.args.get("per_page", None)

    if page_param is not None or per_page_param is not None:
        try:
            page = int(page_param) if page_param is not None else 1
            per_page = int(per_page_param) if per_page_param is not None else 20
            page = max(page, 1)
            per_page = min(max(per_page, 1), 100)
            paginate = True
        except Exception:
            page, per_page = 1, 20
            paginate = True
    else:
        page, per_page = 1, 2000
        paginate = False

    try:
        users, total = UserService.get_users(
            filter_param=filter_param,
            sort=sort,
            page=page,
            per_page=per_page,
            paginate=paginate,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    response_data = {"data": [user.serialize(include, exclude) for user in users]}
    if paginate:
        response_data["page"] = page
        response_data["per_page"] = per_page
        response_data["total"] = total

    return jsonify(response_data), 200


@endpoints.route("/user/<user>", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user(user):
    """
    Retrieve detailed information for a specific user (admin only).

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Access**: Only admin users can view other users' detailed information

    **Path Parameters**:
    - `user`: User identifier (email address or numeric ID)

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include
      - Available: `executions`, `scripts`, `login_history`, `password_last_changed`
    - `exclude`: Comma-separated list of fields to exclude from response
      - Available: `institution`, `country`

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "user-123",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER",
        "country": "US",
        "institution": "Example Organization",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T11:45:00Z",
        "is_active": true,
        "last_login": "2025-01-15T09:30:00Z"
      }
    }
    ```

    **User Roles**:
    - `USER`: Regular user with basic permissions
    - `MANAGER`: Manager with elevated permissions
    - `ADMIN`: Administrator with full system access
    - `SUPERADMIN`: Super administrator with unrestricted access

    **Field Control Examples**:
    - `?include=executions` - Include user's execution count and recent activity
    - `?include=scripts` - Include user's script count and script details
    - `?exclude=institution,country` - Exclude personal information fields

    **Use Cases**:
    - Admin user management and profile review
    - User support and troubleshooting
    - Audit and compliance reporting
    - Account verification and validation

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: User does not exist
    - `500 Internal Server Error`: Failed to retrieve user
    """
    logger.info("[ROUTER]: Getting user" + user)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    identity = current_user
    if not is_admin_or_higher(identity):
        return error(status=403, detail="Forbidden")
    try:
        user = UserService.get_user(user)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize(include, exclude)), 200


@endpoints.route("/user/me", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_me():
    """
    Get current authenticated user's profile information.

    **Authentication**: JWT token required
    **Access**: Returns current user's own profile data

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "user-123",
        "name": "John Doe",
        "email": "john.doe@example.com",
        "role": "USER",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T11:45:00Z",
        "institution": "Conservation International",
        "country": "United States",
        "is_active": true
      }
    }
    ```

    **User Roles**:
    - `USER`: Regular user with basic permissions
    - `MANAGER`: Manager with elevated permissions
    - `ADMIN`: Administrator with full system access
    - `SUPERADMIN`: Super administrator with unrestricted access

    **Error Responses**:
    - `401 Unauthorized`: JWT token required or invalid
    """
    logger.info("[ROUTER]: Getting my user")
    user = current_user
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/me", strict_slashes=False, methods=["PATCH"])
@jwt_required()
def update_profile():
    """
    Update current user's profile information and preferences.

    **Authentication**: JWT token required
    **Access**: Users can only update their own profile information
    **Scope**: Updates current authenticated user's profile data and settings

    **Request Schema**:
    ```json
    {
      "name": "John Smith",
      "country": "CA",
      "institution": "New Research Institute",
      "email_notifications_enabled": false,
      "password": "newSecurePassword123",
      "repeatPassword": "newSecurePassword123"
    }
    ```

    **Updatable Fields**:
    - `name`: User's full name (string)
    - `country`: Two-letter country code (string)
    - `institution`: User's organization/institution (string)
    - `email_notifications_enabled`: Enable/disable email notifications for
      execution completion (boolean)
    - `password`: New password (must include `repeatPassword`)
    - `repeatPassword`: Password confirmation (must match `password`)

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "user-123",
        "email": "user@example.com",
        "name": "John Smith",
        "role": "USER",
        "country": "CA",
        "institution": "New Research Institute",
        "email_notifications_enabled": false,
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T12:00:00Z",
        "is_active": true
      }
      ```

    **Email Notification Behavior**:
    - When `email_notifications_enabled=true` (default): User receives emails
      when executions finish
    - When `email_notifications_enabled=false`: No execution completion emails sent
    - Affects notifications for FINISHED, FAILED, and CANCELLED execution states

    **Password Update Requirements**:
    - Both `password` and `repeatPassword` fields must be provided
    - Passwords must match exactly
    - Password must meet security requirements (length, complexity)
    - Old password is not required for self-service updates

    **Profile Update Rules**:
    - Users cannot change their own role
    - Email address cannot be changed via this endpoint
    - At least one field must be provided for update
    - Changes are applied immediately

    **Validation**:
    - Country codes validated against ISO 3166-1 alpha-2 standard
    - Name and institution have length and character restrictions
    - Password strength requirements enforced

    **Error Responses**:
    - `400 Bad Request`: Invalid data, password mismatch, or no fields to update
    - `401 Unauthorized`: JWT token required
    - `404 Not Found`: User not found
    - `422 Unprocessable Entity`: Validation failed
    - `500 Internal Server Error`: Profile update failed
    """
    logger.info("[ROUTER]: Updating profile")
    body = request.get_json()
    identity = current_user
    try:
        password = body.get("password", None)
        repeat_password = body.get("repeatPassword", None)
        if (
            password is not None
            and repeat_password is not None
            and password == repeat_password
        ):
            user = UserService.update_profile_password(body, identity)
        else:
            if "role" in body:
                del body["role"]
            name = body.get("name", None)
            country = body.get("country", None)
            institution = body.get("institution", None)
            email_notifications_enabled = body.get("email_notifications_enabled", None)

            if (
                name is not None
                or country is not None
                or institution is not None
                or email_notifications_enabled is not None
            ):
                user = UserService.update_user(body, str(identity.id))
            else:
                return error(status=400, detail="Not updated")
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except PasswordValidationError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=422, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/me/change-password", strict_slashes=False, methods=["PATCH"])
@jwt_required()
def change_password():
    """
    Change current user's password with old password verification.

    **Authentication**: JWT token required
    **Access**: Secure password change requiring current password verification
    **Security**: Validates old password before allowing change

    **Request Schema**:
    ```json
    {
      "old_password": "currentPassword123",
      "new_password": "newSecurePassword456"
    }
    ```

    **Request Fields**:
    - `old_password`: Current password for verification (required)
    - `new_password`: New password to set (required)

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "user-123",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T12:30:00Z",
        "password_last_changed": "2025-01-15T12:30:00Z"
      }
    }
    ```

    **Security Features**:
    - Verifies current password before allowing change
    - Enforces password strength requirements
    - Updates password change timestamp
    - Invalidates existing sessions (optional security measure)
    - Logs password change event for audit

    **Password Requirements**:
    - Minimum length (typically 8+ characters)
    - Must include mix of letters, numbers, and special characters
    - Cannot be same as current password
    - Cannot be common or easily guessable passwords

    **Use Cases**:
    - Regular password rotation for security
    - Password change after suspected compromise
    - Compliance with security policies
    - User-initiated security enhancement

    **Error Responses**:
    - `400 Bad Request`: Missing required fields
    - `401 Unauthorized`: JWT token required or incorrect old password
    - `422 Unprocessable Entity`: New password doesn't meet requirements
    - `500 Internal Server Error`: Password change failed
    """
    logger.info("[ROUTER]: Changing password")
    body = request.get_json()
    identity = current_user
    old_password = body.get("old_password")
    new_password = body.get("new_password")

    if not old_password or not new_password:
        return error(status=400, detail="old_password and new_password are required")

    try:
        user = UserService.change_password(identity, old_password, new_password)
    except AuthError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=401, detail=e.message)
    except PasswordValidationError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=422, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/me", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_profile():
    """
    Delete current user's account and all associated data.

    **Authentication**: JWT token required
    **Warning**: This action is irreversible and deletes all user data
    **Scope**: Deletes current authenticated user's account

    **Request**: No request body required

    **Deletion Process**:
    - Cancels all running executions for this user
    - Deletes all execution history and logs
    - Removes user-created scripts (if user is owner)
    - Deletes user profile and authentication data
    - Revokes all active sessions and tokens
    - Removes user from any Google Groups (if enabled)

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "user-123",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T13:00:00Z",
        "deleted_at": "2025-01-15T13:00:00Z",
        "status": "DELETED"
      }
    }
    ```

    **Data Cleanup**:
    - User profile information permanently deleted
    - Execution history and logs removed
    - Script ownership transferred or scripts deleted
    - Session tokens invalidated immediately
    - Email address becomes available for re-registration

    **Important Notes**:
    - Action cannot be undone - all data is permanently lost
    - User will be immediately logged out from all devices
    - Any shared scripts may become inaccessible to other users
    - Admin users cannot delete their own accounts via this endpoint

    **GDPR Compliance**:
    - Implements "right to erasure" requirements
    - Removes all personal data from system
    - Maintains minimal audit log for compliance (anonymized)

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Cannot delete admin accounts via self-service
    - `500 Internal Server Error`: Account deletion failed
    """
    logger.info("[ROUTER]: Delete me")
    identity = current_user
    try:
        user = UserService.delete_user(str(identity.id))
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route(
    "/user/<user>/recover-password", strict_slashes=False, methods=["POST"]
)
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_password_reset_limits()) or "3 per hour",
    key_func=get_admin_aware_key,
    exempt_when=is_rate_limiting_disabled,
)
def recover_password(user):
    """
    Initiate password recovery process for a user account.

    **Rate Limited**: Subject to password recovery rate limits (configurable)
    **Access**: Public endpoint - no authentication required
    **Security**: Rate limited to prevent abuse and email flooding

    **Path Parameters**:
    - `user`: User identifier (email address or numeric ID)

    **Query Parameters**:
    - `legacy`: (optional, default=true) Password recovery mode:
        - `true` (default): Legacy mode - generates new password and emails it
          directly. Maintained for backwards compatibility with older QGIS
          plugin versions.
        - `false`: Secure mode - sends a password reset link that expires after
          1 hour. Recommended for new integrations.

    **Request**: No request body required

    **Recovery Process (legacy=true, default)**:
    1. Validates user exists
    2. Generates a new secure password
    3. Updates user's password in database
    4. Emails the new password to user

    **Recovery Process (legacy=false)**:
    1. Validates user exists and account is active
    2. Generates secure password reset token with 1-hour expiration
    3. Sends password recovery email with reset link
    4. User clicks link and sets new password via /user/reset-password endpoint

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "user-123",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER"
      }
    }
    ```

    **Security Notes**:
    - Legacy mode (default) is DEPRECATED but maintained for backwards
      compatibility. It sends passwords via email which is less secure.
    - New integrations should use `legacy=false` for better security.
    - Rate limiting prevents email flooding attacks in both modes.

    **Error Responses**:
    - `404 Not Found`: User does not exist
    - `429 Too Many Requests`: Rate limit exceeded
    - `500 Internal Server Error`: Email delivery failed or system error
    """
    logger.info("[ROUTER]: Recovering password")

    # Parse legacy parameter - defaults to True for backwards compatibility
    legacy_param = request.args.get("legacy", "true").lower()
    use_legacy = legacy_param not in ("false", "0", "no")

    try:
        user = UserService.recover_password(user, legacy=use_legacy)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except EmailError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=500, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/reset-password", strict_slashes=False, methods=["POST"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_password_reset_limits()) or "3 per hour",
    key_func=get_admin_aware_key,
    exempt_when=is_rate_limiting_disabled,
)
def reset_password_with_token():
    """
    Reset password using a secure token from password recovery email.

    **Rate Limited**: Subject to password recovery rate limits (configurable)
    **Access**: Public endpoint - no authentication required
    **Security**: Token-based authentication, tokens expire after 1 hour

    **Request Body Schema**:
    ```json
    {
      "token": "secure-reset-token-from-email",
      "password": "new-secure-password"
    }
    ```

    **Password Requirements**:
    - Minimum 8 characters
    - Must contain at least one uppercase letter
    - Must contain at least one lowercase letter
    - Must contain at least one digit

    **Success Response Schema**:
    ```json
    {
      "data": {
        "message": "Password reset successful"
      }
    }
    ```

    **Security Features**:
    - Tokens are single-use (marked as used after successful reset)
    - Tokens expire after 1 hour
    - Rate limiting prevents brute force attacks
    - Password strength validation enforced

    **Error Responses**:
    - `400 Bad Request`: Missing token or password
    - `404 Not Found`: Invalid or expired token
    - `422 Unprocessable Entity`: Password doesn't meet requirements
    - `429 Too Many Requests`: Rate limit exceeded
    - `500 Internal Server Error`: System error
    """
    logger.info("[ROUTER]: Reset password with token")
    try:
        body = request.get_json()
        if not body:
            return error(status=400, detail="Request body required")

        token = body.get("token")
        password = body.get("password")

        if not token:
            return error(status=400, detail="Reset token is required")
        if not password:
            return error(status=400, detail="New password is required")

        UserService.reset_password_with_token(token, password)
        return jsonify(data={"message": "Password reset successful"}), 200

    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except PasswordValidationError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=422, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")


@endpoints.route("/user/<user>", strict_slashes=False, methods=["PATCH"])
@jwt_required()
@validate_user_update
def update_user(user):
    """
    Update another user's profile information (admin only).

    **Authentication**: JWT token required
    **Authorization**: Admin level access required for most updates
    **Special Permissions**: Role changes require SUPERADMIN access

    **Path Parameters**:
    - `user`: User identifier (email address or numeric ID) to update

    **Request Schema**:
    ```json
    {
      "name": "Jane Smith",
      "country": "CA",
      "institution": "Updated Research Institute",
      "role": "ADMIN",
      "is_active": true
    }
    ```

    **Updatable Fields**:
    - `name`: User's full name (ADMIN+ can update)
    - `country`: Two-letter country code (ADMIN+ can update)
    - `institution`: User's organization (ADMIN+ can update)
    - `role`: User role (SUPERADMIN only)
    - `is_active`: Account active status (ADMIN+ can update)

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "user-456",
        "email": "user@example.com",
        "name": "Jane Smith",
        "role": "ADMIN",
        "country": "CA",
        "institution": "Updated Research Institute",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T14:00:00Z",
        "is_active": true
      }
    }
    ```

    **Permission Matrix**:
    - **ADMIN**: Can update name, country, institution, is_active
    - **SUPERADMIN**: Can update all fields including role

    **Role Update Rules**:
    - Only SUPERADMIN can change user roles
    - Cannot downgrade another SUPERADMIN (security protection)
    - Role changes are logged for audit purposes
    - Role changes may affect user's active sessions

    **Use Cases**:
    - Admin user account management
    - Account activation/deactivation
    - Profile corrections and updates
    - Role assignments and permissions management

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient permissions for requested update
    - `404 Not Found`: User does not exist
    - `422 Unprocessable Entity`: Validation failed
    - `500 Internal Server Error`: User update failed
    """
    logger.info("[ROUTER]: Updating user" + user)
    body = request.get_json()
    identity = current_user

    # Check if user is trying to update role - only superadmin can do this
    if "role" in body and not can_change_user_role(identity):
        return error(status=403, detail="Forbidden")

    # Check if user can update other user's profile
    if not can_update_user_profile(identity):
        return error(status=403, detail="Forbidden")
    try:
        user = UserService.update_user(body, user)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route("/user/<user>", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_user(user):
    """
    Delete another user's account and all associated data (admin only).

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Warning**: This action is irreversible and deletes all user data

    **Path Parameters**:
    - `user`: User identifier (email address or numeric ID) to delete

    **Protection**: Cannot delete protected admin accounts configured via
    API_ENVIRONMENT_USER

    **Deletion Process**:
    - Cancels all running executions for the target user
    - Deletes all execution history and logs
    - Removes user-created scripts (if user is owner)
    - Deletes user profile and authentication data
    - Revokes all active sessions and tokens
    - Removes user from any Google Groups (if enabled)
    - Transfers or deletes shared resources

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "user-456",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T14:30:00Z",
        "deleted_at": "2025-01-15T14:30:00Z",
        "deleted_by": "admin-123",
        "status": "DELETED"
      }
    }
    ```

    **Administrative Features**:
    - Action is logged with admin user who performed deletion
    - All user data is permanently removed
    - User's scripts and executions are cleaned up
    - Email address becomes available for re-registration

    **Data Cleanup**:
    - User profile information permanently deleted
    - Execution history and logs removed
    - Script ownership transferred or scripts deleted
    - Session tokens invalidated immediately
    - Audit logs maintain record of deletion action

    **Use Cases**:
    - Account cleanup and user management
    - Compliance with data retention policies
    - Removing inactive or problematic accounts
    - GDPR "right to erasure" compliance

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required or cannot delete system admin
    - `404 Not Found`: User does not exist
    - `500 Internal Server Error`: Account deletion failed
    """
    logger.info("[ROUTER]: Deleting user" + user)
    identity = current_user
    if is_protected_admin_email(user):
        return error(status=403, detail="Forbidden")
    if not can_delete_user(identity):
        return error(status=403, detail="Forbidden")
    try:
        user = UserService.delete_user(user)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200


@endpoints.route(
    "/user/<user>/change-password", strict_slashes=False, methods=["PATCH"]
)
@jwt_required()
def admin_change_password(user):
    """
    Change another user's password (admin only).

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Security**: Admin password reset without requiring old password

    **Path Parameters**:
    - `user`: User identifier (email address or numeric ID) for password change

    **Request Schema**:
    ```json
    {
      "new_password": "newSecurePassword123"
    }
    ```

    **Request Fields**:
    - `new_password`: New password to set for the user (required)

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "user-456",
        "email": "user@example.com",
        "name": "John Doe",
        "role": "USER",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T15:00:00Z",
        "password_last_changed": "2025-01-15T15:00:00Z",
        "password_changed_by": "admin-123"
      }
    }
    ```

    **Administrative Features**:
    - Does not require user's current password
    - Updates password change timestamp
    - Records which admin performed the change
    - Enforces same password strength requirements
    - Invalidates user's existing sessions for security

    **Security Considerations**:
    - Action is logged for audit purposes
    - User receives notification of password change
    - All user's active sessions are terminated
    - Password must meet system security requirements

    **Use Cases**:
    - Emergency account recovery for locked users
    - Password reset for users who forgot credentials
    - Security incident response
    - Administrative account maintenance

    **Audit Logging**:
    - Records admin user who changed password
    - Timestamps the password change event
    - Maintains audit trail for compliance
    - May trigger security notifications

    **Error Responses**:
    - `400 Bad Request`: Missing new_password field
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required, or ADMIN trying to change
      SUPERADMIN password
    - `404 Not Found`: User does not exist
    - `422 Unprocessable Entity`: Password doesn't meet requirements
    - `500 Internal Server Error`: Password change failed
    """
    logger.info("[ROUTER]: Admin changing password for user " + user)
    body = request.get_json()
    identity = current_user

    # First check if user has basic permission to change passwords
    if not can_change_user_password(identity):
        return error(status=403, detail="Forbidden")

    new_password = body.get("new_password")
    if not new_password:
        return error(status=400, detail="new_password is required")

    try:
        target_user = UserService.get_user(user)

        # Check if admin can change this specific user's password
        # (ADMIN cannot change SUPERADMIN passwords)
        if not can_admin_change_user_password(identity, target_user):
            logger.warning(
                f"[ROUTER]: Admin {identity.email} attempted to change "
                f"SUPERADMIN {target_user.email}'s password"
            )
            return error(
                status=403,
                detail="Administrators cannot change superadmin passwords",
            )

        user = UserService.admin_change_password(target_user, new_password)
    except UserNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except PasswordValidationError as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=422, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=user.serialize()), 200
