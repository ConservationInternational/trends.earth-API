"""API routes for Google Earth Engine credential management"""

import json
import logging
import os

from flask import jsonify, request
from flask_jwt_extended import current_user, get_jwt_identity, jwt_required

from gefapi import db
from gefapi.models.user import User
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.gee_service import GEEService
from gefapi.utils.permissions import is_admin_or_higher

logger = logging.getLogger(__name__)


@endpoints.route("/user/me/gee-credentials", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user_gee_credentials():
    """
    Get current user's Google Earth Engine credentials status.

    **Authentication**: JWT token required
    **Authorization**: Any authenticated user

    **Response Schema**:
    ```json
    {
      "data": {
        "has_credentials": true,
        "credentials_type": "service_account",
        "created_at": "2025-01-15T10:30:00Z"
      }
    }
    ```

    **Response Fields**:
    - `has_credentials`: Boolean indicating if user has GEE credentials configured
    - `credentials_type`: Type of credentials ("oauth" or "service_account"), or null
    - `created_at`: ISO timestamp when credentials were last set, null if none

    **Error Responses**:
    - `401 Unauthorized`: JWT token required or invalid
    - `404 Not Found`: User not found
    - `500 Internal Server Error`: Server error occurred
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)

        if not user:
            return error(status=404, detail="User not found")

        return jsonify(
            {
                "data": {
                    "has_credentials": user.has_gee_credentials(),
                    "credentials_type": user.gee_credentials_type,
                    "created_at": user.gee_credentials_created_at.isoformat()
                    if user.gee_credentials_created_at
                    else None,
                }
            }
        )

    except Exception as e:
        logger.error(f"Error getting GEE credentials status: {e}")
        return error(status=500, detail="Internal server error")


@endpoints.route("/user/me/gee-oauth/initiate", strict_slashes=False, methods=["POST"])
@jwt_required()
def initiate_gee_oauth():
    """
    Initiate OAuth flow for Google Earth Engine authentication.

    **Authentication**: JWT token required
    **Authorization**: Any authenticated user

    **Prerequisites**:
    - Server must have Google OAuth client credentials configured
    - Environment variables GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET
      required

    **Response Schema**:
    ```json
    {
      "data": {
        "auth_url": "https://accounts.google.com/o/oauth2/auth?...",
        "state": "random-state-string-for-csrf-protection"
      }
    }
    ```

    **Response Fields**:
    - `auth_url`: URL to redirect user to for Google OAuth authorization
    - `state`: CSRF protection token to include in callback

    **OAuth Flow Steps**:
    1. Call this endpoint to get authorization URL
    2. Redirect user to the auth_url
    3. User authorizes your application in Google
    4. User is redirected back with authorization code
    5. Call `/user/me/gee-oauth/callback` with the code and state

    **Error Responses**:
    - `401 Unauthorized`: JWT token required or invalid
    - `500 Internal Server Error`: OAuth not configured or server error
    """
    try:
        # Check if OAuth client credentials are configured
        client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        if not client_id:
            return error(status=500, detail="OAuth not configured")

        from google_auth_oauthlib.flow import Flow

        # OAuth configuration
        oauth_config = {
            "web": {
                "client_id": client_id,
                "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [
                    os.getenv(
                        "GOOGLE_OAUTH_REDIRECT_URI",
                        "http://localhost:3000/api/v1/user/me/gee-oauth/callback",
                    )
                ],
            }
        }

        # Create OAuth flow
        flow = Flow.from_client_config(
            oauth_config, scopes=["https://www.googleapis.com/auth/earthengine"]
        )
        flow.redirect_uri = oauth_config["web"]["redirect_uris"][0]

        # Generate authorization URL
        auth_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )

        return jsonify({"data": {"auth_url": auth_url, "state": state}})

    except Exception as e:
        logger.error(f"Error initiating OAuth flow: {e}")
        return error(status=500, detail="Failed to initiate OAuth flow")


@endpoints.route("/user/me/gee-oauth/callback", strict_slashes=False, methods=["POST"])
@jwt_required()
def handle_gee_oauth_callback():
    """
    Complete OAuth flow and store Google Earth Engine credentials.

    **Authentication**: JWT token required
    **Authorization**: Any authenticated user
    **Content-Type**: application/json

    **Request Body Schema**:
    ```json
    {
      "code": "authorization_code_from_google",
      "state": "state_token_from_initiate_call"
    }
    ```

    **Required Fields**:
    - `code`: Authorization code received from Google OAuth callback
    - `state`: State token from the initiate call for CSRF protection

    **Response Schema**:
    ```json
    {
      "message": "GEE OAuth credentials saved successfully"
    }
    ```

    **Error Responses**:
    - `400 Bad Request`: Missing code/state, invalid code, or JSON parsing error
    - `401 Unauthorized`: JWT token required or invalid
    - `404 Not Found`: User not found
    - `500 Internal Server Error`: Failed to exchange code or save credentials
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)

        if not user:
            return error(status=404, detail="User not found")

        json_data = request.get_json()
        if not json_data:
            return error(status=400, detail="JSON data required")

        # Validate required fields
        if "code" not in json_data:
            return error(status=400, detail="Authorization code is required")

        if "state" not in json_data:
            return error(status=400, detail="State parameter is required")

        # Exchange authorization code for tokens
        from google_auth_oauthlib.flow import Flow

        client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        oauth_config = {
            "web": {
                "client_id": client_id,
                "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [
                    os.getenv(
                        "GOOGLE_OAUTH_REDIRECT_URI",
                        "http://localhost:3000/api/v1/user/me/gee-oauth/callback",
                    )
                ],
            }
        }

        flow = Flow.from_client_config(
            oauth_config,
            scopes=["https://www.googleapis.com/auth/earthengine"],
            state=json_data["state"],
        )
        flow.redirect_uri = oauth_config["web"]["redirect_uris"][0]

        # Fetch tokens
        flow.fetch_token(code=json_data["code"])

        credentials = flow.credentials

        # Store credentials
        user.set_gee_oauth_credentials(
            access_token=credentials.token, refresh_token=credentials.refresh_token
        )

        db.session.commit()

        logger.info(f"Successfully stored GEE OAuth credentials for user {user.email}")

        return jsonify({"message": "GEE OAuth credentials saved successfully"})

    except Exception as e:
        logger.error(f"Error handling OAuth callback: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save OAuth credentials")


@endpoints.route("/user/me/gee-service-account", strict_slashes=False, methods=["POST"])
@jwt_required()
def upload_gee_service_account():
    """
    Upload Google Earth Engine service account credentials.

    **Authentication**: JWT token required
    **Authorization**: Any authenticated user
    **Content-Type**: application/json

    **Request Body Schema**:
    ```json
    {
      "service_account_key": {
        "type": "service_account",
        "project_id": "your-gee-project",
        "private_key_id": "key-id",
        "private_key": "-----BEGIN PRIVATE KEY-----...-----END PRIVATE KEY-----\\n",
        "client_email": "service-account@your-gee-project.iam.gserviceaccount.com",
        "client_id": "client-id",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/..."
      }
    }
    ```

    **Required Fields**:
    - `service_account_key`: Google service account JSON key object or JSON string

    **Service Account Key Requirements**:
    - Must be a valid Google Cloud service account key
    - Must have Google Earth Engine API access enabled
    - Should have appropriate permissions for your GEE project
    - Can be provided as JSON object or JSON string

    **Response Schema**:
    ```json
    {
      "message": "GEE service account credentials saved successfully"
    }
    ```

    **Security Notes**:
    - Service account keys are encrypted before storage
    - Keys should be generated specifically for Trends.Earth use
    - Rotate keys regularly following Google Cloud security best practices

    **Error Responses**:
    - `400 Bad Request`: Missing/invalid service account key, or validation failed
    - `401 Unauthorized`: JWT token required or invalid
    - `404 Not Found`: User not found
    - `500 Internal Server Error`: Failed to save credentials
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)

        if not user:
            return error(status=404, detail="User not found")

        json_data = request.get_json()
        if not json_data:
            return error(status=400, detail="JSON data required")

        # Validate required fields
        if "service_account_key" not in json_data:
            return error(status=400, detail="Service account key is required")

        service_account_key = json_data["service_account_key"]

        # Parse JSON if it's a string
        if isinstance(service_account_key, str):
            try:
                service_account_key = json.loads(service_account_key)
            except json.JSONDecodeError:
                return error(
                    status=400, detail="Invalid JSON format for service account key"
                )

        # Validate service account key
        if not GEEService.validate_service_account_key(service_account_key):
            return error(status=400, detail="Invalid service account key format")

        # Store service account credentials
        user.set_gee_service_account(service_account_key)
        db.session.commit()

        logger.info(f"Successfully stored GEE service account for user {user.email}")

        return jsonify(
            {"message": "GEE service account credentials saved successfully"}
        )

    except Exception as e:
        logger.error(f"Error uploading service account: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save service account credentials")


@endpoints.route("/user/me/gee-credentials", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_gee_credentials():
    """
    Delete current user's Google Earth Engine credentials.

    **Authentication**: JWT token required
    **Authorization**: Any authenticated user

    **Response Schema**:
    ```json
    {
      "message": "GEE credentials deleted successfully"
    }
    ```

    **What Gets Deleted**:
    - OAuth access and refresh tokens (if using OAuth)
    - Service account key (if using service account)
    - Credentials type and creation timestamp
    - All encrypted credential data is permanently removed

    **Error Responses**:
    - `401 Unauthorized`: JWT token required or invalid
    - `404 Not Found`: User not found or no GEE credentials exist
    - `500 Internal Server Error`: Failed to delete credentials
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)

        if not user:
            return error(status=404, detail="User not found")

        if not user.has_gee_credentials():
            return error(status=404, detail="No GEE credentials found")

        # Clear credentials
        user.clear_gee_credentials()
        db.session.commit()

        logger.info(f"Successfully deleted GEE credentials for user {user.email}")

        return jsonify({"message": "GEE credentials deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting GEE credentials: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to delete GEE credentials")


@endpoints.route(
    "/user/me/gee-credentials/test", strict_slashes=False, methods=["POST"]
)
@jwt_required()
def test_gee_credentials():
    """
    Test current user's Google Earth Engine credentials.

    **Authentication**: JWT token required
    **Authorization**: Any authenticated user

    **Prerequisites**:
    - User must have GEE credentials configured (OAuth or service account)

    **Response Schema (Success)**:
    ```json
    {
      "message": "GEE credentials are valid and working"
    }
    ```

    **What This Tests**:
    - Initializes Google Earth Engine with user's credentials
    - Verifies credentials are not expired
    - Confirms GEE API access is working
    - Validates credential format and permissions

    **Typical Workflow**:
    1. Check if credentials exist using GET /user/me/gee-credentials
    2. Test credentials validity using this endpoint
    3. If credentials are valid, proceed with GEE analysis
    4. If credentials are invalid/expired, refresh or update credentials

    **Error Responses**:
    - `400 Bad Request`: GEE credentials not configured or invalid/expired
    - `401 Unauthorized`: JWT token required or invalid
    - `404 Not Found`: User not found
    - `500 Internal Server Error`: Failed to test credentials
    """
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)

        if not user:
            return error(status=404, detail="User not found")

        if not user.has_gee_credentials():
            return error(status=400, detail="No GEE credentials configured")

        # Test credentials by initializing GEE
        if GEEService._initialize_ee(user):
            return jsonify({"message": "GEE credentials are valid and working"})
        return error(status=400, detail="GEE credentials are invalid or expired")

    except Exception as e:
        logger.error(f"Error testing GEE credentials: {e}")
        return error(status=500, detail="Failed to test GEE credentials")


# Admin endpoints for managing other users' GEE credentials


@endpoints.route(
    "/user/<user_id>/gee-credentials", strict_slashes=False, methods=["GET"]
)
@jwt_required()
def get_user_gee_credentials_admin(user_id):
    """
    Get another user's Google Earth Engine credentials status (Admin only).

    **Authentication**: JWT token required
    **Authorization**: ADMIN or SUPERADMIN role required

    **Path Parameters**:
    - `user_id`: Target user's ID (string or integer)

    **Response Schema**:
    ```json
    {
      "data": {
        "user_id": "user-123",
        "user_email": "user@example.com",
        "has_credentials": true,
        "credentials_type": "service_account",
        "created_at": "2025-01-15T10:30:00Z"
      }
    }
    ```

    **Response Fields**:
    - `user_id`: Target user's ID
    - `user_email`: Target user's email address
    - `has_credentials`: Boolean indicating if user has GEE credentials
    - `credentials_type`: Type of credentials ("oauth" or "service_account"), or null
    - `created_at`: ISO timestamp when credentials were last set, null if none

    **Error Responses**:
    - `401 Unauthorized`: JWT token required or invalid
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: User not found
    - `500 Internal Server Error`: Server error occurred
    """
    try:
        # Check admin permissions
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin access required")

        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        return jsonify(
            {
                "data": {
                    "user_id": user.id,
                    "user_email": user.email,
                    "has_credentials": user.has_gee_credentials(),
                    "credentials_type": user.gee_credentials_type,
                    "created_at": user.gee_credentials_created_at.isoformat()
                    if user.gee_credentials_created_at
                    else None,
                }
            }
        )

    except Exception as e:
        logger.error(f"Error getting user GEE credentials status: {e}")
        return error(status=500, detail="Internal server error")


@endpoints.route(
    "/user/<user_id>/gee-service-account", strict_slashes=False, methods=["POST"]
)
@jwt_required()
def upload_user_gee_service_account_admin(user_id):
    """
    Upload Google Earth Engine service account for another user (Admin only).

    **Authentication**: JWT token required
    **Authorization**: ADMIN or SUPERADMIN role required
    **Content-Type**: application/json

    **Path Parameters**:
    - `user_id`: Target user's ID (string or integer)

    **Request Body Schema**:
    ```json
    {
      "service_account_key": {
        "type": "service_account",
        "project_id": "your-gee-project",
        "private_key_id": "key-id",
        "private_key": "-----BEGIN PRIVATE KEY-----...-----END PRIVATE KEY-----\\n",
        "client_email": "service-account@your-gee-project.iam.gserviceaccount.com",
        "client_id": "client-id",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/..."
      }
    }
    ```

    **Required Fields**:
    - `service_account_key`: Complete service account JSON key object or JSON string

    **Response Schema**:
    ```json
    {
      "message": "GEE service account credentials saved for user user@example.com"
    }
    ```

    **Admin Use Cases**:
    - Provide organizational GEE access to users
    - Set up shared service account for team projects
    - Replace expired or compromised credentials
    - Migrate users from individual to shared credentials

    **Security & Audit**:
    - Admin action is logged with both admin and target user details
    - Service account keys are encrypted before storage
    - Replaces any existing credentials for the user

    **Error Responses**:
    - `400 Bad Request`: Missing service account, invalid JSON, or validation failed
    - `401 Unauthorized`: JWT token required or invalid
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: Target user not found
    - `500 Internal Server Error`: Failed to save credentials
    """
    try:
        # Check admin permissions
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin access required")

        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        json_data = request.get_json()
        if not json_data:
            return error(status=400, detail="JSON data required")

        # Validate required fields
        if "service_account_key" not in json_data:
            return error(status=400, detail="Service account key is required")

        service_account_key = json_data["service_account_key"]

        # Parse JSON if it's a string
        if isinstance(service_account_key, str):
            try:
                service_account_key = json.loads(service_account_key)
            except json.JSONDecodeError:
                return error(
                    status=400, detail="Invalid JSON format for service account key"
                )

        # Validate service account key
        if not GEEService.validate_service_account_key(service_account_key):
            return error(status=400, detail="Invalid service account key format")

        # Store service account credentials
        user.set_gee_service_account(service_account_key)
        db.session.commit()

        logger.info(
            f"Admin {current_user.email} set GEE service account for user {user.email}"
        )

        return jsonify(
            {"message": f"GEE service account credentials saved for user {user.email}"}
        )

    except Exception as e:
        logger.error(f"Error uploading service account for user {user_id}: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save service account credentials")


@endpoints.route(
    "/user/<user_id>/gee-credentials", strict_slashes=False, methods=["DELETE"]
)
@jwt_required()
def delete_user_gee_credentials_admin(user_id):
    """
    Delete another user's Google Earth Engine credentials (Admin only).

    **Authentication**: JWT token required
    **Authorization**: ADMIN or SUPERADMIN role required

    **Path Parameters**:
    - `user_id`: Target user's ID (string or integer)

    **Response Schema**:
    ```json
    {
      "message": "GEE credentials deleted for user user@example.com"
    }
    ```

    **What Gets Deleted**:
    - All OAuth tokens (access and refresh tokens)
    - Service account credentials
    - Credentials type and metadata
    - All encrypted credential data is permanently removed

    **Admin Use Cases**:
    - Revoke access for users leaving the organization
    - Clean up expired or compromised credentials
    - Force credential refresh by removing and re-adding
    - Audit and compliance requirements

    **Security & Audit**:
    - Admin action is logged with both admin and target user details
    - Irreversible operation - credentials cannot be recovered
    - User will need to reconfigure GEE credentials to regain access

    **Error Responses**:
    - `401 Unauthorized`: JWT token required or invalid
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: Target user not found or user has no GEE credentials
    - `500 Internal Server Error`: Failed to delete credentials
    """
    try:
        # Check admin permissions
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin access required")

        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        if not user.has_gee_credentials():
            return error(status=404, detail="No GEE credentials found for user")

        # Clear credentials
        user.clear_gee_credentials()
        db.session.commit()

        logger.info(
            f"Admin {current_user.email} deleted GEE credentials for user {user.email}"
        )

        return jsonify({"message": f"GEE credentials deleted for user {user.email}"})

    except Exception as e:
        logger.error(f"Error deleting GEE credentials for user {user_id}: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to delete GEE credentials")


@endpoints.route(
    "/user/<user_id>/gee-credentials/test", strict_slashes=False, methods=["POST"]
)
@jwt_required()
def test_user_gee_credentials_admin(user_id):
    """
    Test another user's Google Earth Engine credentials (Admin only).

    **Authentication**: JWT token required
    **Authorization**: ADMIN or SUPERADMIN role required

    **Path Parameters**:
    - `user_id`: Target user's ID (string or integer)

    **Prerequisites**:
    - Target user must have GEE credentials configured

    **Response Schema (Success)**:
    ```json
    {
      "message": "GEE credentials for user user@example.com are valid and working"
    }
    ```

    **What This Tests**:
    - Initializes Google Earth Engine with the user's credentials
    - Verifies credentials are not expired
    - Confirms GEE API access is working
    - Validates credential format and permissions

    **Admin Use Cases**:
    - Validate credentials after setup/update
    - Troubleshoot user access issues
    - Periodic credential health checks
    - Pre-execution validation for GEE scripts

    **Error Responses**:
    - `400 Bad Request`: No GEE credentials or credentials are invalid/expired
    - `401 Unauthorized`: JWT token required or invalid
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: Target user not found
    - `500 Internal Server Error`: Failed to test credentials
    """
    try:
        # Check admin permissions
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin access required")

        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        if not user.has_gee_credentials():
            return error(status=400, detail="No GEE credentials configured for user")

        # Test credentials by initializing GEE
        if GEEService._initialize_ee(user):
            return jsonify(
                {
                    "message": (
                        f"GEE credentials for user {user.email} are valid and working"
                    )
                }
            )
        return error(
            status=400,
            detail=f"GEE credentials for user {user.email} are invalid or expired",
        )

    except Exception as e:
        logger.error(f"Error testing GEE credentials for user {user_id}: {e}")
        return error(status=500, detail="Failed to test GEE credentials")
