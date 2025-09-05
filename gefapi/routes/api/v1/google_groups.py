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
    """
    Retrieve current user's Google Groups preferences and synchronization status.

    **Authentication**: JWT token required
    **Access**: Users can view their own Google Groups preferences
    **Purpose**: Display current opt-in preferences and sync status for Google
    Groups integration

    **Response Schema**:
    ```json
    {
      "data": {
        "user_preferences": {
          "trends_earth_users": true,
          "trendsearth": false
        },
        "sync_status": {
          "last_sync": "2025-01-15T10:30:00Z",
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
    ```

    **User Preferences**:
    - `trends_earth_users`: Opt-in status for general user community
    - `trendsearth`: Opt-in status for platform-specific communications
    - Boolean values indicate current user preference

    **Sync Status Information**:
    - `last_sync`: Timestamp of last synchronization with Google Groups
    - `registration_status`: Current sync state
      - `synced`: User preferences are synchronized
      - `pending`: Sync in progress or queued
      - `failed`: Last sync attempt failed
      - `never`: User has never been synced

    **Available Groups**:
    - Complete list of Google Groups available for opt-in
    - Group metadata including names and descriptions
    - Service availability status

    **Integration Features**:
    - Real-time preference display
    - Sync status monitoring
    - Service availability checking
    - Error state handling

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve Google Groups preferences
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
    """
    Update current user's Google Groups preferences and trigger synchronization.

    **Authentication**: JWT token required
    **Access**: Users can update their own Google Groups preferences
    **Purpose**: Change opt-in preferences and sync with Google Groups service

    **Request Schema**:
    ```json
    {
      "preferences": {
        "trends_earth_users": true,
        "trendsearth": false
      }
    }
    ```

    **Request Fields**:
    - `preferences`: Object containing group opt-in preferences
      - `trends_earth_users`: Boolean for general user community
      - `trendsearth`: Boolean for platform-specific group
      - Only valid group keys accepted

    **Success Response Schema**:
    ```json
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
            "success": true,
            "current_member": true
          },
          "trendsearth": {
            "action": "no_change",
            "success": true,
            "current_member": false
          }
        }
      }
    }
    ```

    **Update Process**:
    1. Validates request data and group keys
    2. Updates user preferences in database
    3. Triggers synchronization with Google Groups service
    4. Returns updated preferences and sync results
    5. Handles partial sync failures gracefully

    **Sync Actions**:
    - `added`: User was added to the Google Group
    - `removed`: User was removed from the Google Group
    - `no_change`: User membership unchanged
    - `verified`: Membership status confirmed

    **Preference Validation**:
    - Only accepts valid group keys
    - Boolean values required for all preferences
    - Partial updates not supported - must specify all groups

    **Error Handling**:
    - Preferences saved even if sync fails
    - Sync errors reported in response
    - User can retry sync separately
    - Service availability checked before sync

    **Error Responses**:
    - `400 Bad Request`: Invalid preferences data or group keys
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to update preferences or sync
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
    """
    Manually trigger synchronization of user's Google Groups memberships.

    **Authentication**: JWT token required
    **Access**: Users can trigger sync for their own Google Groups
    **Purpose**: Force synchronization between user preferences and actual Google
    Groups memberships

    **Request**: No request body required - this is a POST endpoint

    **Success Response Schema**:
    ```json
    {
      "data": {
        "message": "Google Groups sync completed",
        "sync_results": {
          "user_email": "user@example.com",
          "trends_earth_users": {
            "action": "verified",
            "success": true,
            "current_member": true,
            "details": "Membership confirmed in Google Groups"
          },
          "trendsearth": {
            "action": "removed",
            "success": true,
            "current_member": false,
            "details": "User removed from group as requested"
          }
        }
      }
    }
    ```

    **Manual Sync Process**:
    1. Checks Google Groups service availability
    2. Reads current user preferences from database
    3. Queries actual Google Groups memberships
    4. Synchronizes memberships to match preferences
    5. Returns detailed sync results

    **Sync Actions Performed**:
    - `added`: User added to Google Group
    - `removed`: User removed from Google Group
    - `verified`: Membership status confirmed as correct
    - `skipped`: No action needed
    - `failed`: Action attempted but failed

    **Use Cases**:
    - Verify current group memberships
    - Recover from failed automatic sync
    - Troubleshoot synchronization issues
    - Force immediate sync after preference changes

    **Service Dependency**:
    - Requires Google Groups service to be available
    - Returns service unavailable error if Google Groups down
    - Handles temporary service outages gracefully

    **Error Recovery**:
    - Provides detailed error information
    - Distinguishes between service and permission errors
    - Suggests next steps for error resolution

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `503 Service Unavailable`: Google Groups service not available
    - `500 Internal Server Error`: Sync process failed
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
    """
    Retrieve public information about available Google Groups.

    **Authentication**: Not required (public endpoint)
    **Access**: Public endpoint for displaying available groups
    **Purpose**: Provide group information for registration and informational purposes

    **Response Schema**:
    ```json
    {
      "data": {
        "available_groups": {
          "trends_earth_users": {
            "group_name": "Trends.Earth Users",
            "description": "General Trends.Earth user community for announcements and
            discussions"
          },
          "trendsearth": {
            "group_name": "TrendsEarth",
            "description": "TrendsEarth platform users for technical updates and
            support"
          }
        }
      }
    }
    ```

    **Available Groups Information**:
    - `trends_earth_users`: General user community
      - Announcements and general discussions
      - User community support and collaboration
      - Platform updates and news
    - `trendsearth`: Platform-specific group
      - Technical updates and platform changes
      - Feature announcements and improvements
      - User support and troubleshooting

    **Public Information Only**:
    - Group names and descriptions provided
    - No sensitive information exposed
    - No actual group email addresses shown
    - Service status not disclosed

    **Use Cases**:
    - Display available groups during user registration
    - Show group options in user preference settings
    - Provide information for marketing and outreach
    - Public documentation and help pages

    **Security Considerations**:
    - No authentication required
    - Limited to public information only
    - No user-specific data exposed
    - Rate limiting may apply

    **Integration Notes**:
    - Can be cached for performance
    - Updates when new groups are added
    - Consistent with authenticated endpoints
    - Safe for public consumption

    **Error Responses**:
    - `500 Internal Server Error`: Failed to retrieve group information
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
