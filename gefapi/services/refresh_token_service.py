"""REFRESH TOKEN SERVICE"""

import datetime
import logging

from flask import request
from flask_jwt_extended import create_access_token

from gefapi import db
from gefapi.models.refresh_token import RefreshToken

logger = logging.getLogger(__name__)


class RefreshTokenService:
    """Refresh Token Service"""

    @staticmethod
    def create_refresh_token(user_id, device_info=None):
        """Create a new refresh token for a user"""
        logger.info(f"[SERVICE]: Creating refresh token for user {user_id}")

        # Get device info from request if not provided
        if device_info is None:
            device_info = RefreshTokenService._get_device_info()

        refresh_token = RefreshToken(user_id=user_id, device_info=device_info)

        try:
            db.session.add(refresh_token)
            db.session.commit()
            logger.info(
                f"[SERVICE]: Refresh token created successfully for user {user_id}"
            )
            return refresh_token
        except Exception as error:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error creating refresh token: {error}")
            raise error

    @staticmethod
    def validate_refresh_token(token_string):
        """Validate a refresh token and return the associated user"""
        logger.info("[SERVICE]: Validating refresh token")

        refresh_token = RefreshToken.query.filter_by(token=token_string).first()

        if not refresh_token:
            logger.warning("[SERVICE]: Refresh token not found")
            return None, None

        if not refresh_token.is_valid():
            logger.warning("[SERVICE]: Refresh token is invalid (expired or revoked)")
            return None, None

        # Update last used timestamp
        refresh_token.update_last_used()
        db.session.commit()

        user = refresh_token.user

        # Update user's last_activity_at timestamp
        try:
            import datetime

            user.last_activity_at = datetime.datetime.utcnow()
            db.session.commit()
            logger.debug(f"[SERVICE]: Updated last_activity_at for user {user.email}")
        except Exception as e:
            # Don't fail token validation if we can't update the timestamp
            logger.warning(
                f"[SERVICE]: Failed to update last_activity_at for {user.email}: {e}"
            )
            db.session.rollback()

        logger.info(f"[SERVICE]: Refresh token validated for user {user.email}")
        return refresh_token, user

    @staticmethod
    def refresh_access_token(refresh_token_string):
        """Generate a new access token using a valid refresh token"""
        logger.info("[SERVICE]: Refreshing access token")

        refresh_token, user = RefreshTokenService.validate_refresh_token(
            refresh_token_string
        )

        if not refresh_token or not user:
            logger.warning("[SERVICE]: Invalid refresh token provided")
            return None, None

        # Generate new access token
        access_token = create_access_token(identity=user.id)

        logger.info(f"[SERVICE]: Access token refreshed for user {user.email}")
        return access_token, user

    @staticmethod
    def revoke_refresh_token(token_string):
        """Revoke a specific refresh token"""
        logger.info("[SERVICE]: Revoking refresh token")

        refresh_token = RefreshToken.query.filter_by(token=token_string).first()

        if not refresh_token:
            logger.warning("[SERVICE]: Refresh token not found for revocation")
            return False

        refresh_token.revoke()

        try:
            db.session.commit()
            logger.info("[SERVICE]: Refresh token revoked successfully")
            return True
        except Exception as error:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error revoking refresh token: {error}")
            raise error

    @staticmethod
    def revoke_all_user_tokens(user_id):
        """Revoke all refresh tokens for a specific user"""
        logger.info(f"[SERVICE]: Revoking all refresh tokens for user {user_id}")

        refresh_tokens = RefreshToken.query.filter_by(
            user_id=user_id, is_revoked=False
        ).all()

        for token in refresh_tokens:
            token.revoke()

        try:
            db.session.commit()
            logger.info(
                f"[SERVICE]: Revoked {len(refresh_tokens)} refresh tokens for "
                f"user {user_id}"
            )
            return len(refresh_tokens)
        except Exception as error:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error revoking user tokens: {error}")
            raise error

    @staticmethod
    def get_user_active_sessions(user_id):
        """Get all active refresh tokens (sessions) for a user"""
        logger.info(f"[SERVICE]: Getting active sessions for user {user_id}")

        active_tokens = (
            RefreshToken.query.filter_by(user_id=user_id, is_revoked=False)
            .filter(RefreshToken.expires_at > datetime.datetime.utcnow())
            .all()
        )

        return active_tokens

    @staticmethod
    def invalidate_user_sessions(user_id, current_session_token=None):
        """Invalidate all sessions except current session"""
        logger.info(f"[SERVICE]: Invalidating user sessions for user {user_id}")

        refresh_tokens = RefreshToken.query.filter_by(
            user_id=user_id, is_revoked=False
        ).all()

        revoked_count = 0
        for token in refresh_tokens:
            if current_session_token and token.token == current_session_token:
                continue  # Keep current session
            token.revoke()
            revoked_count += 1

        try:
            db.session.commit()
            logger.info(
                f"[SERVICE]: Invalidated {revoked_count} user sessions for "
                f"user {user_id}"
            )
            return revoked_count
        except Exception as error:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error invalidating user sessions: {error}")
            raise error

    @staticmethod
    def cleanup_expired_tokens():
        """Clean up expired refresh tokens (should be run periodically)"""
        logger.info("[SERVICE]: Cleaning up expired refresh tokens")

        expired_tokens = RefreshToken.query.filter(
            RefreshToken.expires_at <= datetime.datetime.utcnow()
        ).all()

        for token in expired_tokens:
            db.session.delete(token)

        try:
            db.session.commit()
            logger.info(
                f"[SERVICE]: Cleaned up {len(expired_tokens)} expired refresh tokens"
            )
            return len(expired_tokens)
        except Exception as error:
            db.session.rollback()
            logger.error(f"[SERVICE]: Error cleaning up expired tokens: {error}")
            raise error

    @staticmethod
    def _get_device_info():
        """Extract device information from the request"""
        try:
            # Try to get information from Flask request context
            from flask import has_request_context

            if has_request_context():
                user_agent = request.headers.get("User-Agent", "Unknown")
                ip_address = request.remote_addr or "Unknown"
            else:
                # Fallback for testing or non-request contexts
                user_agent = "Test Environment"
                ip_address = "127.0.0.1"
        except Exception:
            # Fallback for any other issues
            user_agent = "Unknown"
            ip_address = "Unknown"

        # Truncate to fit database field
        device_info = f"IP: {ip_address} | UA: {user_agent}"[:500]
        return device_info
