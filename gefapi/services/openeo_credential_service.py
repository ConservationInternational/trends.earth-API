"""openEO Credential Service for managing openEO backend authentication."""

import logging

import rollbar

from gefapi.config import SETTINGS

logger = logging.getLogger(__name__)

# Import openeo at module level for test mocking, but allow ImportError
try:
    import openeo  # type: ignore
except ImportError:
    openeo = None


class OpenEOCredentialService:
    """Service for managing openEO backend authentication.

    Credential priority order:
    1. Per-user credentials stored on the User model
    2. System-level OIDC refresh token (OPENEO_REFRESH_TOKEN env var)
    3. System-level basic auth (OPENEO_USERNAME / OPENEO_PASSWORD)
    4. Anonymous (no authentication)
    """

    @staticmethod
    def _validate_backend_url(url: str) -> str:
        """Validate that *url* is an https:// URL and return it.

        Rejects non-https schemes to prevent SSRF via admin-controlled
        Script.openeo_backend_url or misconfigured settings.
        """
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError(
                f"openEO backend URL must use https://, got: '{url}'. "
                "Non-https schemes are not permitted."
            )
        if not parsed.netloc:
            raise ValueError(f"openEO backend URL has no host: '{url}'.")
        return url

    @staticmethod
    def _resolve_backend_url(script=None, environment: dict | None = None) -> str:
        """Return the effective openEO backend URL.

        Priority: script.openeo_backend_url > env var > SETTINGS default.
        All resolved URLs are validated to use https://.
        """
        if script is not None and getattr(script, "openeo_backend_url", None):
            return OpenEOCredentialService._validate_backend_url(
                script.openeo_backend_url
            )

        if environment:
            url = environment.get("OPENEO_BACKEND_URL")
            if url:
                return OpenEOCredentialService._validate_backend_url(url)

        url = SETTINGS.get("OPENEO_DEFAULT_BACKEND_URL")
        if url:
            return OpenEOCredentialService._validate_backend_url(url)

        raise ValueError(
            "No openEO backend URL configured.  Set OPENEO_DEFAULT_BACKEND_URL "
            "or provide a per-script openeo_backend_url."
        )

    @staticmethod
    def connect(user=None, backend_url: str | None = None) -> "openeo.Connection":
        """Open an authenticated connection to the openEO backend.

        Args:
            user: Optional User model instance.  When supplied, per-user
                  credentials take priority over system credentials.
            backend_url: Override the backend URL.  When omitted, resolved
                         from SETTINGS.

        Returns:
            An authenticated (or anonymous) openeo.Connection.
        """
        if openeo is None:
            raise ImportError(
                "The 'openeo' package is not installed.  "
                "Add it to pyproject.toml dependencies."
            )

        if backend_url is None:
            backend_url = OpenEOCredentialService._resolve_backend_url()

        conn = openeo.connect(backend_url)

        # 1. Per-user credentials
        if user is not None and user.has_openeo_credentials():
            try:
                creds = user.get_openeo_credentials()
                if creds:
                    cred_type = creds.get("type", "oidc_refresh_token")
                    if cred_type == "oidc_refresh_token":
                        provider_id = creds.get("provider_id", "egi")
                        client_id = creds.get("client_id", "")
                        client_secret = creds.get("client_secret", "")
                        refresh_token = creds.get("refresh_token", "")
                        conn.authenticate_oidc_refresh_token(
                            client_id=client_id,
                            client_secret=client_secret,
                            refresh_token=refresh_token,
                            provider_id=provider_id,
                        )
                        logger.info(
                            "Authenticated to openEO as user %s via OIDC refresh token",
                            user.email,
                        )
                        return conn
                    if cred_type == "basic":
                        username = creds.get("username", "")
                        password = creds.get("password", "")
                        conn.authenticate_basic(username, password)
                        logger.info(
                            "Authenticated to openEO as user %s via basic auth",
                            user.email,
                        )
                        return conn
            except Exception as exc:
                logger.warning(
                    "Failed to authenticate to openEO with user credentials for "
                    "%s: %s.  Falling back to system credentials.",
                    getattr(user, "email", "unknown"),
                    exc,
                )

        # 2. System OIDC refresh token
        env_settings = SETTINGS.get("environment") or {}
        refresh_token = env_settings.get("OPENEO_REFRESH_TOKEN")
        if refresh_token:
            try:
                client_id = env_settings.get("OPENEO_CLIENT_ID", "")
                client_secret = env_settings.get("OPENEO_CLIENT_SECRET", "")
                provider_id = env_settings.get("OPENEO_PROVIDER_ID", "egi")
                conn.authenticate_oidc_refresh_token(
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=refresh_token,
                    provider_id=provider_id,
                )
                logger.info("Authenticated to openEO backend via system OIDC token.")
                return conn
            except Exception as exc:
                logger.warning(
                    "System OIDC authentication failed: %s.  Trying basic auth.",
                    exc,
                )

        # 3. System basic auth
        openeo_username = env_settings.get("OPENEO_USERNAME")
        openeo_password = env_settings.get("OPENEO_PASSWORD")
        if openeo_username and openeo_password:
            try:
                conn.authenticate_basic(openeo_username, openeo_password)
                logger.info("Authenticated to openEO backend via system basic auth.")
                return conn
            except Exception as exc:
                logger.warning(
                    "System basic auth to openEO failed: %s.  "
                    "Using anonymous connection.",
                    exc,
                )

        # 4. Anonymous
        logger.info("Using anonymous openEO connection (no credentials configured).")
        return conn

    @staticmethod
    def validate_credentials(user) -> bool:
        """Test whether the user's stored openEO credentials work.

        Returns True if a connection can be established successfully.
        """
        if openeo is None:
            logger.error("openeo package not available")
            return False

        try:
            conn = OpenEOCredentialService.connect(user=user)
            # A cheap call to verify the connection is alive
            conn.describe_account()
            return True
        except Exception as exc:
            logger.warning(
                "openEO credential validation failed for user %s: %s",
                getattr(user, "email", "unknown"),
                exc,
            )
            rollbar.report_exc_info()
            return False
