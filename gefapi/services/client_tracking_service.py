"""Client tracking service for parsing and storing client metadata."""

import logging

from flask import has_request_context, request

from gefapi import db
from gefapi.models.user_client_metadata import UserClientMetadata

logger = logging.getLogger(__name__)

# Header name for client identification
CLIENT_HEADER_NAME = "X-TE-Client"

# Valid client types
VALID_CLIENT_TYPES = {"qgis_plugin", "api_ui", "cli"}


class ClientTrackingService:
    """Service for tracking client platform and version usage."""

    @staticmethod
    def parse_client_header(header_value: str) -> dict | None:
        """Parse the X-TE-Client header into structured data.

        Header format: key=value; key=value; ...
        Example: type=qgis_plugin; version=2.2.4; qgis_version=3.34.0; os=Windows

        Returns:
            dict with parsed values, or None if invalid
        """
        if not header_value:
            return None

        try:
            result = {}
            # Split by semicolon, then parse key=value pairs
            for part in header_value.split(";"):
                part = part.strip()
                if "=" in part:
                    key, value = part.split("=", 1)
                    result[key.strip()] = value.strip()

            # Validate required field
            client_type = result.get("type")
            if not client_type or client_type not in VALID_CLIENT_TYPES:
                logger.warning(
                    f"[ClientTracking] Invalid or missing client type: {client_type}"
                )
                return None

            return result
        except Exception as e:
            logger.warning(
                f"[ClientTracking] Failed to parse header '{header_value}': {e}"
            )
            return None

    @staticmethod
    def get_client_info_from_request() -> dict | None:
        """Extract client info from the current request's X-TE-Client header.

        Returns:
            Parsed client info dict, or None if header missing/invalid
        """
        if not has_request_context():
            return None

        header_value = request.headers.get(CLIENT_HEADER_NAME)
        if not header_value:
            return None

        return ClientTrackingService.parse_client_header(header_value)

    @staticmethod
    def track_client_access(
        user_id, client_info: dict | None = None
    ) -> UserClientMetadata | None:
        """Track a client access for a user.

        Creates or updates the UserClientMetadata row for this user+client_type.

        Args:
            user_id: The user's ID
            client_info: Parsed client info dict (if None, extracts from request)

        Returns:
            The UserClientMetadata record, or None if no client info available
        """
        if client_info is None:
            client_info = ClientTrackingService.get_client_info_from_request()

        if not client_info:
            return None

        client_type = client_info.get("type")
        if not client_type:
            return None

        try:
            # Find existing record for this user+client_type
            existing = UserClientMetadata.query.filter_by(
                user_id=user_id,
                client_type=client_type,
            ).first()

            # Extract fields from client_info
            client_version = client_info.get("version")
            os_name = client_info.get("os")
            qgis_version = client_info.get("qgis_version")
            language = client_info.get("lang")

            # Build extra_metadata from any unrecognized fields
            known_fields = {"type", "version", "os", "qgis_version", "lang"}
            extra = {k: v for k, v in client_info.items() if k not in known_fields}
            extra_metadata = extra if extra else None

            if existing:
                # Update existing record
                existing.update_from_header(
                    client_version=client_version,
                    os=os_name,
                    qgis_version=qgis_version,
                    language=language,
                    extra_metadata=extra_metadata,
                )
                db.session.commit()
                logger.debug(
                    f"[ClientTracking] Updated metadata for user {user_id}, "
                    f"client {client_type} v{client_version}"
                )
                return existing
            # Create new record
            metadata = UserClientMetadata(
                user_id=user_id,
                client_type=client_type,
                client_version=client_version,
                os=os_name,
                qgis_version=qgis_version,
                language=language,
                extra_metadata=extra_metadata,
            )
            db.session.add(metadata)
            db.session.commit()
            logger.info(
                f"[ClientTracking] Created metadata for user {user_id}, "
                f"client {client_type} v{client_version}"
            )
            return metadata

        except Exception as e:
            db.session.rollback()
            logger.error(f"[ClientTracking] Error tracking client access: {e}")
            return None

    @staticmethod
    def get_user_clients(user_id) -> list:
        """Get all client metadata records for a user.

        Returns:
            List of UserClientMetadata records
        """
        return UserClientMetadata.query.filter_by(user_id=user_id).all()
