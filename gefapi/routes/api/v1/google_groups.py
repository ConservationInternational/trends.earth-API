"""Google Groups API endpoints"""

import logging

from flask import jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi import db
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.google_groups_service import google_groups_service

logger = logging.getLogger(__name__)


@endpoints.route("/user/me/google-groups", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user_google_groups_preferences():
    """Get current user's Google Groups preferences and sync status

    Returns the user's current Google Groups opt-in preferences, sync status,
    and information about available groups.

    Example Response:
    {
        "data": {
            "user_preferences": {
                "trends_earth_users": true,
                "trendsearth": false
            },
            "sync_status": {
                "last_sync": "2025-07-31T10:30:00Z",
                "registration_status": "synced"
            },
            "available_groups": {
                "trends_earth_users": {
                    "group_name": "Trends.Earth Users",
                    "description": "General Trends.Earth user community",
                    "group_email": "trends-earth-users@conservationinternational.org"
                },
                "trendsearth": {
                    "group_name": "TrendsEarth",
                    "description": "TrendsEarth platform users",
                    "group_email": "trendsearth@conservationinternational.org"
                }
            },
            "service_available": true
        }
    }
    """
    try:
        user = current_user

        # Get group info and user preferences
        group_info = google_groups_service.get_group_info()

        response_data = {
            "user_preferences": {
                "trends_earth_users": user.google_groups_trends_earth_users,
                "trendsearth": user.google_groups_trendsearth,
            },
            "sync_status": {
                "last_sync": user.google_groups_last_sync.isoformat()
                if user.google_groups_last_sync
                else None,
                "registration_status": user.google_groups_registration_status,
            },
            "available_groups": group_info["available_groups"],
            "service_available": group_info["service_available"],
        }

        return jsonify(data=response_data), 200

    except Exception as e:
        logger.error(f"Error getting Google Groups preferences: {e}")
        return error(status=500, detail="Failed to get Google Groups preferences")


@endpoints.route("/user/me/google-groups", strict_slashes=False, methods=["PUT"])
@jwt_required()
def update_user_google_groups_preferences():
    """Update current user's Google Groups preferences and sync with Google Groups

    Updates the user's opt-in preferences for Google Groups and triggers a sync
    with the Google Groups service to add/remove memberships accordingly.

    Request Body:
    {
        "preferences": {
            "trends_earth_users": true,
            "trendsearth": false
        }
    }

    Example Response:
    {
        "data": {
            "message": "Google Groups preferences updated successfully",
            "updated_groups": ["trends_earth_users"],
            "preferences": {
                "trends_earth_users": true,
                "trendsearth": false
            },
            "sync_results": {
                "user_email": "user@example.com",
                "trends_earth_users": {
                    "action": "added",
                    "success": true
                },
                "trendsearth": {
                    "action": "no_change",
                    "success": true
                }
            }
        }
    }
    """
    try:
        user = current_user
        data = request.get_json()

        if not data:
            return error(status=400, detail="Request body is required")

        # Validate request data
        valid_groups = ["trends_earth_users", "trendsearth"]
        preferences = data.get("preferences", {})

        if not isinstance(preferences, dict):
            return error(status=400, detail="'preferences' must be an object")

        # Validate that only valid group keys are provided
        invalid_keys = set(preferences.keys()) - set(valid_groups)
        if invalid_keys:
            invalid_keys_list = list(invalid_keys)
            detail_msg = (
                f"Invalid group keys: {invalid_keys_list}. Valid keys: {valid_groups}"
            )
            return error(status=400, detail=detail_msg)

        # Update user preferences
        updated_groups = []
        for group_key, opt_in in preferences.items():
            if not isinstance(opt_in, bool):
                return error(
                    status=400, detail=f"Preference for '{group_key}' must be a boolean"
                )

            # Update user model
            current_value = getattr(user, f"google_groups_{group_key}")
            if current_value != opt_in:
                setattr(user, f"google_groups_{group_key}", opt_in)
                updated_groups.append(group_key)

        # Save changes to database
        try:
            db.session.commit()
            logger.info(
                f"Updated Google Groups preferences for user {user.email}: "
                f"{preferences}"
            )
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save user preferences: {e}")
            return error(status=500, detail="Failed to save preferences")

        # Sync with Google Groups (async in background would be better for production)
        sync_results = None
        if google_groups_service.is_available():
            try:
                sync_results = google_groups_service.sync_user_groups(user)
                logger.info(f"Synced Google Groups for user {user.email}")
            except Exception as e:
                logger.error(f"Failed to sync Google Groups for user {user.email}: {e}")
                # Don't fail the request if sync fails - preferences are still saved
                sync_results = {
                    "error": f"Sync failed: {str(e)}",
                    "user_email": user.email,
                }
        else:
            logger.warning(
                "Google Groups service not available - preferences saved but not synced"
            )

        response_data = {
            "message": "Google Groups preferences updated successfully",
            "updated_groups": updated_groups,
            "preferences": {
                "trends_earth_users": user.google_groups_trends_earth_users,
                "trendsearth": user.google_groups_trendsearth,
            },
            "sync_results": sync_results,
        }

        return jsonify(data=response_data), 200

    except Exception as e:
        logger.error(f"Error updating Google Groups preferences: {e}")
        return error(status=500, detail="Failed to update Google Groups preferences")


@endpoints.route("/user/me/google-groups/sync", strict_slashes=False, methods=["POST"])
@jwt_required()
def sync_user_google_groups():
    """Manually trigger sync of user's Google Groups memberships

    Forces a synchronization between the user's current preferences and their
    actual Google Groups memberships. This is useful if automatic sync failed
    or to verify current membership status.

    Example Response:
    {
        "data": {
            "message": "Google Groups sync completed",
            "sync_results": {
                "user_email": "user@example.com",
                "trends_earth_users": {
                    "action": "verified",
                    "success": true,
                    "current_member": true
                },
                "trendsearth": {
                    "action": "removed",
                    "success": true,
                    "current_member": false
                }
            }
        }
    }
    """
    try:
        user = current_user

        if not google_groups_service.is_available():
            return error(status=503, detail="Google Groups service is not available")

        # Perform sync
        sync_results = google_groups_service.sync_user_groups(user)

        response_data = {
            "message": "Google Groups sync completed",
            "sync_results": sync_results,
        }

        return jsonify(data=response_data), 200

    except Exception as e:
        logger.error(f"Error syncing Google Groups: {e}")
        return error(status=500, detail="Failed to sync Google Groups")


@endpoints.route("/google-groups/info", strict_slashes=False, methods=["GET"])
def get_google_groups_info():
    """Get information about available Google Groups (public endpoint)

    Returns public information about available Google Groups that users can
    opt into. This endpoint does not require authentication and provides
    basic group information for display purposes.

    Example Response:
    {
        "data": {
            "available_groups": {
                "trends_earth_users": {
                    "group_name": "Trends.Earth Users",
                    "description": "General Trends.Earth user community"
                },
                "trendsearth": {
                    "group_name": "TrendsEarth",
                    "description": "TrendsEarth platform users"
                }
            }
        }
    }
    """
    try:
        group_info = google_groups_service.get_group_info()

        # Return only public information (no service status)
        response_data = {
            "available_groups": {
                key: {
                    "group_name": value["group_name"],
                    "description": value["description"],
                    # Don't expose actual group emails for security
                }
                for key, value in group_info["available_groups"].items()
            }
        }

        return jsonify(data=response_data), 200

    except Exception as e:
        logger.error(f"Error getting Google Groups info: {e}")
        return error(status=500, detail="Failed to get Google Groups information")
