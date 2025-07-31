"""GOOGLE GROUPS SERVICE"""

from datetime import datetime
import json
import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gefapi import db
from gefapi.config import SETTINGS

logger = logging.getLogger(__name__)

# Google Groups configuration
GOOGLE_GROUPS_CONFIG = {
    "trends_earth_users": {
        "group_email": "trends_earth_users@googlegroups.com",
        "group_name": "Trends.Earth Users",
        "description": "Community group for Trends.Earth users",
    },
    "trendsearth": {
        "group_email": "trendsearth@googlegroups.com",
        "group_name": "TrendsEarth",
        "description": "TrendsEarth community group",
    },
}


class GoogleGroupsService:
    """Service for managing Google Groups memberships"""

    def __init__(self):
        self.service = None
        self._initialize_service()

    def _initialize_service(self):
        """Initialize Google Admin SDK service"""
        try:
            # Load service account credentials from settings
            credentials_info = SETTINGS.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS")
            if not credentials_info:
                logger.warning("Google service account credentials not configured")
                return

            # If credentials are stored as JSON string, parse them
            if isinstance(credentials_info, str):
                credentials_info = json.loads(credentials_info)

            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=["https://www.googleapis.com/auth/admin.directory.group"],
            )

            # Create the Admin SDK service
            self.service = build("admin", "directory_v1", credentials=credentials)
            logger.info("Google Groups service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Google Groups service: {e}")
            self.service = None

    def is_available(self) -> bool:
        """Check if Google Groups service is available"""
        return self.service is not None

    def add_user_to_group(self, user_email: str, group_key: str) -> dict:
        """Add a user to a Google Group

        Args:
            user_email: Email of the user to add
            group_key: Group identifier (trends_earth_users or trendsearth)

        Returns:
            Dict with success status and details
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "Google Groups service not available",
                "group": group_key,
            }

        if group_key not in GOOGLE_GROUPS_CONFIG:
            return {
                "success": False,
                "error": f"Unknown group: {group_key}",
                "group": group_key,
            }

        group_email = GOOGLE_GROUPS_CONFIG[group_key]["group_email"]

        try:
            # Add member to group
            member_body = {
                "email": user_email,
                "role": "MEMBER",  # Can be OWNER, MANAGER, MEMBER
            }

            result = (
                self.service.members()
                .insert(groupKey=group_email, body=member_body)
                .execute()
            )

            logger.info(f"Successfully added {user_email} to {group_email}")
            return {
                "success": True,
                "group": group_key,
                "group_email": group_email,
                "member_id": result.get("id"),
                "timestamp": datetime.utcnow().isoformat(),
            }

        except HttpError as e:
            # Handle specific Google API errors
            error_details = e._get_reason() if hasattr(e, "_get_reason") else str(e)

            # Check if user is already a member
            if e.resp.status == 409:  # Conflict - already a member
                logger.info(f"User {user_email} is already a member of {group_email}")
                return {
                    "success": True,
                    "group": group_key,
                    "group_email": group_email,
                    "already_member": True,
                    "timestamp": datetime.utcnow().isoformat(),
                }

            logger.error(
                f"Failed to add {user_email} to {group_email}: {error_details}"
            )
            return {
                "success": False,
                "error": error_details,
                "group": group_key,
                "group_email": group_email,
            }

        except Exception as e:
            logger.error(f"Unexpected error adding {user_email} to {group_key}: {e}")
            return {"success": False, "error": str(e), "group": group_key}

    def remove_user_from_group(self, user_email: str, group_key: str) -> dict:
        """Remove a user from a Google Group"""
        if not self.is_available():
            return {
                "success": False,
                "error": "Google Groups service not available",
                "group": group_key,
            }

        if group_key not in GOOGLE_GROUPS_CONFIG:
            return {
                "success": False,
                "error": f"Unknown group: {group_key}",
                "group": group_key,
            }

        group_email = GOOGLE_GROUPS_CONFIG[group_key]["group_email"]

        try:
            self.service.members().delete(
                groupKey=group_email, memberKey=user_email
            ).execute()

            logger.info(f"Successfully removed {user_email} from {group_email}")
            return {
                "success": True,
                "group": group_key,
                "group_email": group_email,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except HttpError as e:
            error_details = e._get_reason() if hasattr(e, "_get_reason") else str(e)

            # If user is not a member, consider it success
            if e.resp.status == 404:
                logger.info(f"User {user_email} was not a member of {group_email}")
                return {
                    "success": True,
                    "group": group_key,
                    "group_email": group_email,
                    "not_member": True,
                    "timestamp": datetime.utcnow().isoformat(),
                }

            logger.error(
                f"Failed to remove {user_email} from {group_email}: {error_details}"
            )
            return {"success": False, "error": error_details, "group": group_key}

        except Exception as e:
            logger.error(
                f"Unexpected error removing {user_email} from {group_key}: {e}"
            )
            return {"success": False, "error": str(e), "group": group_key}

    def sync_user_groups(self, user) -> dict:
        """Sync user's Google Groups memberships based on their preferences

        Args:
            user: User model instance

        Returns:
            Dict with sync results for each group
        """
        results = {
            "user_email": user.email,
            "timestamp": datetime.utcnow().isoformat(),
            "groups": {},
        }

        # Process each group
        for group_key in GOOGLE_GROUPS_CONFIG:
            user_wants_group = getattr(user, f"google_groups_{group_key}", False)

            if user_wants_group:
                # User wants to be in this group - add them
                result = self.add_user_to_group(user.email, group_key)
            else:
                # User doesn't want this group - remove them
                result = self.remove_user_from_group(user.email, group_key)

            results["groups"][group_key] = result

        # Update user's sync status
        user.google_groups_registration_status = json.dumps(results)
        user.google_groups_last_sync = datetime.utcnow()

        try:
            db.session.commit()
            logger.info(f"Updated Google Groups sync status for user {user.email}")
        except Exception as e:
            logger.error(f"Failed to update user sync status: {e}")
            db.session.rollback()

        return results

    def get_group_info(self) -> dict:
        """Get information about available groups"""
        return {
            "available_groups": GOOGLE_GROUPS_CONFIG,
            "service_available": self.is_available(),
        }


# Global service instance
google_groups_service = GoogleGroupsService()
