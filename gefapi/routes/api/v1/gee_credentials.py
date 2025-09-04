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
    """Get current user's GEE credentials status"""
    try:
        user_id = get_jwt_identity()
        user = User.query.get(user_id)

        if not user:
            return error(status=404, detail="User not found")

        return jsonify({
            "data": {
                "has_credentials": user.has_gee_credentials(),
                "credentials_type": user.gee_credentials_type,
                "created_at": user.gee_credentials_created_at.isoformat()
                if user.gee_credentials_created_at else None
            }
        })

    except Exception as e:
        logger.error(f"Error getting GEE credentials status: {e}")
        return error(status=500, detail="Internal server error")


@endpoints.route("/user/me/gee-oauth/initiate", strict_slashes=False, methods=["POST"])
@jwt_required()
def initiate_gee_oauth():
    """Initiate OAuth flow for GEE authentication"""
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
                "redirect_uris": [os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:3000/api/v1/user/me/gee-oauth/callback")]
            }
        }

        # Create OAuth flow
        flow = Flow.from_client_config(
            oauth_config,
            scopes=["https://www.googleapis.com/auth/earthengine"]
        )
        flow.redirect_uri = oauth_config["web"]["redirect_uris"][0]

        # Generate authorization URL
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent"
        )

        return jsonify({
            "data": {
                "auth_url": auth_url,
                "state": state
            }
        })

    except Exception as e:
        logger.error(f"Error initiating OAuth flow: {e}")
        return error(status=500, detail="Failed to initiate OAuth flow")


@endpoints.route("/user/me/gee-oauth/callback", strict_slashes=False, methods=["POST"])
@jwt_required()
def handle_gee_oauth_callback():
    """Handle OAuth callback and store credentials"""
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
                "redirect_uris": [os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:3000/api/v1/user/me/gee-oauth/callback")]
            }
        }

        flow = Flow.from_client_config(
            oauth_config,
            scopes=["https://www.googleapis.com/auth/earthengine"],
            state=json_data["state"]
        )
        flow.redirect_uri = oauth_config["web"]["redirect_uris"][0]

        # Fetch tokens
        flow.fetch_token(code=json_data["code"])

        credentials = flow.credentials

        # Store credentials
        user.set_gee_oauth_credentials(
            access_token=credentials.token,
            refresh_token=credentials.refresh_token
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
    """Upload GEE service account credentials"""
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

        return jsonify({
            "message": "GEE service account credentials saved successfully"
        })

    except Exception as e:
        logger.error(f"Error uploading service account: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save service account credentials")


@endpoints.route("/user/me/gee-credentials", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_gee_credentials():
    """Delete user's GEE credentials"""
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
    """Test user's GEE credentials"""
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
    """Get another user's GEE credentials status (Admin only)"""
    try:
        # Check admin permissions
        if not is_admin_or_higher(current_user):
            return error(status=403, detail="Admin access required")

        user = User.query.get(user_id)
        if not user:
            return error(status=404, detail="User not found")

        return jsonify({
            "data": {
                "user_id": user.id,
                "user_email": user.email,
                "has_credentials": user.has_gee_credentials(),
                "credentials_type": user.gee_credentials_type,
                "created_at": user.gee_credentials_created_at.isoformat()
                if user.gee_credentials_created_at else None
            }
        })

    except Exception as e:
        logger.error(f"Error getting user GEE credentials status: {e}")
        return error(status=500, detail="Internal server error")


@endpoints.route(
    "/user/<user_id>/gee-service-account", strict_slashes=False, methods=["POST"]
)
@jwt_required()
def upload_user_gee_service_account_admin(user_id):
    """Upload GEE service account credentials for another user (Admin only)"""
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

        return jsonify({
            "message": f"GEE service account credentials saved for user {user.email}"
        })

    except Exception as e:
        logger.error(f"Error uploading service account for user {user_id}: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save service account credentials")


@endpoints.route(
    "/user/<user_id>/gee-credentials", strict_slashes=False, methods=["DELETE"]
)
@jwt_required()
def delete_user_gee_credentials_admin(user_id):
    """Delete another user's GEE credentials (Admin only)"""
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

        return jsonify({
            "message": f"GEE credentials deleted for user {user.email}"
        })

    except Exception as e:
        logger.error(f"Error deleting GEE credentials for user {user_id}: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to delete GEE credentials")


@endpoints.route(
    "/user/<user_id>/gee-credentials/test", strict_slashes=False, methods=["POST"]
)
@jwt_required()
def test_user_gee_credentials_admin(user_id):
    """Test another user's GEE credentials (Admin only)"""
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
            return jsonify({
                "message": (
                    f"GEE credentials for user {user.email} are valid and working"
                )
            })
        return error(
            status=400,
            detail=f"GEE credentials for user {user.email} are invalid or expired"
        )

    except Exception as e:
        logger.error(f"Error testing GEE credentials for user {user_id}: {e}")
        return error(status=500, detail="Failed to test GEE credentials")
