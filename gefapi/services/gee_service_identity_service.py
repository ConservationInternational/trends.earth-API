"""OAuth user bucket access management for Earth Engine.

When users run Earth Engine tasks via OAuth, tasks execute under the user's
Google account identity, NOT via a service account. This module manages
granting bucket write access directly to the user's Google account.
"""

import logging

logger = logging.getLogger(__name__)


def get_user_email_from_oauth_token(
    access_token: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    token_uri: str = "https://oauth2.googleapis.com/token",
) -> str | None:
    """Extract the user's email address from their OAuth credentials.

    This calls the Google OAuth2 userinfo endpoint to get the authenticated
    user's email address, which is needed to grant them bucket write access.

    Args:
        access_token: User's OAuth access token
        refresh_token: User's OAuth refresh token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        token_uri: OAuth token endpoint URL

    Returns:
        The user's email address, or None if it couldn't be retrieved.
    """
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials

        # Build credentials from user's OAuth tokens
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/earthengine"],
        )

        # Get user info including email
        session = AuthorizedSession(credentials)
        resp = session.get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=10)

        if resp.status_code == 200:
            user_info = resp.json()
            email = user_info.get("email")
            if email:
                logger.info("Retrieved email %s from OAuth userinfo", email)
                return email
            logger.warning("OAuth userinfo response missing email field: %s", user_info)
            return None
        logger.warning(
            "Failed to get OAuth userinfo (HTTP %d): %s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    except Exception as exc:
        logger.warning("Failed to get user email from OAuth token: %s", exc)
        return None
