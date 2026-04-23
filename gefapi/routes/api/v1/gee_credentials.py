"""API routes for Google Earth Engine credential management"""

import json
import logging
import os
import re
import secrets

from flask import jsonify, request
from flask_jwt_extended import current_user, get_jwt_identity, jwt_required

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models.user import User
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services.gee_service import GEEService
from gefapi.utils import mask_email
from gefapi.utils.permissions import is_admin_or_higher
from gefapi.utils.scopes import require_scope

logger = logging.getLogger(__name__)

# GCP project IDs are lowercase letters, digits, and hyphens, 6–30 chars,
# starting with a letter and not ending with a hyphen.
_GCP_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9\-]{4,28}[a-z0-9]$")

# GCP project numbers are positive integers up to ~12 digits.
_GCP_PROJECT_NUMBER_MIN = 1
_GCP_PROJECT_NUMBER_MAX = 10**13

# ---------------------------------------------------------------------------
# Server-side OAuth state storage (Redis with in-memory fallback)
# ---------------------------------------------------------------------------
_OAUTH_STATE_TTL = 600  # 10 minutes
_oauth_state_store: dict[str, str] = {}  # in-memory fallback

# OAuth scopes requested during the GEE consent flow.
# earthengine  — required for EE API access.
# cloudplatformprojects.readonly — lets us enumerate the user's GCP projects
#   so the UI can offer a project-selection dropdown after OAuth completes.
_GEE_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloudplatformprojects.readonly",
]


def _get_redis_client():
    """Return a Redis client or None when unavailable."""
    try:
        import redis as _redis_mod

        redis_url = SETTINGS.get("CELERY_BROKER_URL")
        if not redis_url:
            return None
        client = _redis_mod.from_url(redis_url)
        client.ping()
        return client
    except Exception:
        return None


def _store_oauth_state(
    user_id: str,
    state: str,
    code_verifier: str | None = None,
    gee_cloud_project: str | None = None,
) -> None:
    """Persist an OAuth state token (and optional PKCE code_verifier) keyed by user ID.

    Also stores the PKCE ``code_verifier`` when the library generates one, and the
    user-supplied ``gee_cloud_project`` so it can be retrieved at callback time.
    """
    key = f"oauth_state:{user_id}"
    value = json.dumps(
        {
            "state": state,
            "code_verifier": code_verifier,
            "gee_cloud_project": gee_cloud_project,
        }
    )
    client = _get_redis_client()
    if client:
        client.setex(key, _OAUTH_STATE_TTL, value)
    else:
        _oauth_state_store[key] = value


def _verify_and_consume_oauth_state(
    user_id: str, state: str
) -> tuple[bool, str | None, str | None]:
    """Validate *state* and return ``(True, code_verifier, gee_cloud_project)``
    on success.

    Returns ``(False, None, None)`` on mismatch. The stored entry is consumed
    on success.
    """
    key = f"oauth_state:{user_id}"
    client = _get_redis_client()
    if client:
        stored = client.get(key)
        if stored is None:
            return False, None, None
        stored_str = stored.decode() if isinstance(stored, bytes) else stored
        try:
            stored_data = json.loads(stored_str)
            stored_state = stored_data.get("state", stored_str)
            code_verifier = stored_data.get("code_verifier")
            gee_cloud_project = stored_data.get("gee_cloud_project")
        except (json.JSONDecodeError, AttributeError):
            stored_state = stored_str
            code_verifier = None
            gee_cloud_project = None
        if not secrets.compare_digest(stored_state, state):
            return False, None, None
        client.delete(key)
        return True, code_verifier, gee_cloud_project

    stored = _oauth_state_store.pop(key, None)
    if stored is None:
        return False, None, None
    try:
        stored_data = json.loads(stored)
        stored_state = stored_data.get("state", stored)
        code_verifier = stored_data.get("code_verifier")
        gee_cloud_project = stored_data.get("gee_cloud_project")
    except (json.JSONDecodeError, AttributeError):
        stored_state = stored
        code_verifier = None
        gee_cloud_project = None
    if not secrets.compare_digest(stored_state, state):
        _oauth_state_store[key] = stored  # restore — state mismatch, not consumed
        return False, None, None
    return True, code_verifier, gee_cloud_project


@endpoints.route("/user/me/gee-credentials", strict_slashes=False, methods=["GET"])
@jwt_required()
@require_scope("gee:read")
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
                    "cloud_project": user.gee_cloud_project
                    if user.gee_credentials_type == "oauth"
                    else None,
                }
            }
        )

    except Exception as e:
        logger.error(f"Error getting GEE credentials status: {e}")
        return error(status=500, detail="Internal server error")


@endpoints.route("/user/me/gee-oauth/initiate", strict_slashes=False, methods=["POST"])
@jwt_required()
@require_scope("gee:write")
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
        flow = Flow.from_client_config(oauth_config, scopes=_GEE_OAUTH_SCOPES)
        flow.redirect_uri = oauth_config["web"]["redirect_uris"][0]

        # Generate authorization URL
        auth_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )

        # Capture the PKCE code_verifier that google-auth-oauthlib auto-generates.
        # authorization_url() stores it at flow.code_verifier
        # (autogenerate_code_verifier=True by default). The verifier must be
        # round-tripped to the callback so that flow.fetch_token() can send it
        # to Google's token endpoint.
        code_verifier = flow.code_verifier

        # Persist state (and code_verifier) server-side so the callback can verify it
        user_id = get_jwt_identity()
        _store_oauth_state(str(user_id), state, code_verifier=code_verifier)

        return jsonify({"data": {"auth_url": auth_url, "state": state}})

    except Exception as e:
        logger.error(f"Error initiating OAuth flow: {e}")
        return error(status=500, detail="Failed to initiate OAuth flow")


@endpoints.route("/user/me/gee-oauth/callback", strict_slashes=False, methods=["POST"])
@jwt_required()
@require_scope("gee:write")
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

        # Verify the state against the server-side stored value (CSRF check)
        state_valid, code_verifier, _ = _verify_and_consume_oauth_state(
            str(user_id), json_data["state"]
        )
        if not state_valid:
            logger.warning(f"OAuth state mismatch for user {user_id} — possible CSRF")
            return error(status=400, detail="Invalid or expired state parameter")

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
            scopes=_GEE_OAUTH_SCOPES,
            state=json_data["state"],
        )
        flow.redirect_uri = oauth_config["web"]["redirect_uris"][0]

        # Fetch tokens — include code_verifier if PKCE was used during initiation
        fetch_kwargs = {"code": json_data["code"]}
        if code_verifier:
            fetch_kwargs["code_verifier"] = code_verifier
        flow.fetch_token(**fetch_kwargs)

        credentials = flow.credentials

        # Store credentials
        user.set_gee_oauth_credentials(
            access_token=credentials.token, refresh_token=credentials.refresh_token
        )

        db.session.commit()

        masked = mask_email(user.email)
        logger.info(f"Successfully stored GEE OAuth credentials for user {masked}")

        return jsonify({"message": "GEE OAuth credentials saved successfully"})

    except Exception as e:
        logger.error(f"Error handling OAuth callback: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save OAuth credentials")


@endpoints.route("/user/me/gee-projects", strict_slashes=False, methods=["GET"])
@jwt_required()
@require_scope("gee:read")
def list_user_gee_projects():
    """List accessible GCP projects via the current user's OAuth credentials.

    Calls the Cloud Resource Manager v3 API with the user's stored token.
    Requires the ``cloudplatformprojects.readonly`` scope to have been granted
    during the OAuth consent flow.

    Returns a JSON array of ``{"value": projectId, "label": displayName (projectId)}``
    objects for all ACTIVE projects, plus ``"current"`` with the already-saved
    project ID (may be ``null``).
    """
    try:
        user = current_user
        if not user:
            return error(status=404, detail="User not found")

        if user.gee_credentials_type != "oauth":
            return error(
                status=400,
                detail="GCP project listing requires OAuth credentials. "
                "Please connect your GEE account first.",
            )

        access_token, refresh_token, cloud_project = user.get_gee_oauth_credentials()
        if not access_token or not refresh_token:
            return error(status=400, detail="OAuth tokens not found")

        from google.auth.exceptions import RefreshError as _RefreshError
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials as _Credentials

        env_settings = SETTINGS.get("environment", {})
        credentials = _Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=env_settings.get(
                "GOOGLE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token"
            ),
            client_id=env_settings.get("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=env_settings.get("GOOGLE_OAUTH_CLIENT_SECRET"),
            scopes=_GEE_OAUTH_SCOPES,
        )

        try:
            session = AuthorizedSession(credentials)
            resp = session.get(
                "https://cloudresourcemanager.googleapis.com/v3/projects",
                params={"pageSize": 100},
                timeout=15,
            )
        except _RefreshError as e:
            logger.warning(
                f"OAuth token refresh failed for user {mask_email(user.email)}: {e}"
            )
            return error(
                status=401,
                detail="OAuth token expired. Please reconnect your GEE account.",
            )

        if not resp.ok:
            logger.error(
                f"Cloud Resource Manager API error {resp.status_code} for "
                f"user {mask_email(user.email)}: {resp.text[:200]}"
            )
            return error(
                status=502,
                detail="Failed to fetch GCP projects. Ensure your Google account "
                "has access to at least one GCP project with the Earth Engine "
                "API enabled.",
            )

        data = resp.json()
        projects = [
            {
                "value": p["projectId"],
                "label": f"{p.get('displayName') or p['projectId']} ({p['projectId']})",
            }
            for p in data.get("projects", [])
            if p.get("state") == "ACTIVE"
        ]
        projects.sort(key=lambda p: p["label"].lower())
        return jsonify({"data": projects, "current": cloud_project})

    except Exception as e:
        logger.error(f"Error listing GCP projects: {e}")
        return error(status=500, detail="Failed to list GCP projects")


@endpoints.route(
    "/user/me/gee-credentials/project", strict_slashes=False, methods=["PATCH"]
)
@jwt_required()
@require_scope("gee:write")
def set_gee_cloud_project():
    """Save the user's selected GEE Cloud Project ID and grant the GEE service
    agent write access to the output GCS bucket.

    The project ID must correspond to a GCP project that has the Earth Engine
    API enabled and that the user has access to.

    **Request body** (JSON):
    - ``cloud_project`` (str, required): GCP project ID (e.g. ``my-gee-project``)
    - ``project_number`` (int, optional): Numeric GCP project number.  When
      supplied the CRM lookup is skipped.  Required when the user's OAuth token
      lacks the ``cloudplatformprojects.readonly`` scope (i.e. when the project
      dropdown could not be populated and manual entry was used).

    **Response** adds ``gcs_write_access`` (bool) to indicate whether the IAM
    grant succeeded.  When ``false`` a ``detail`` field explains why.
    """
    try:
        user = current_user
        if not user:
            return error(status=404, detail="User not found")

        if user.gee_credentials_type != "oauth":
            return error(
                status=400,
                detail="Cloud project selection only applies to OAuth credentials.",
            )

        json_data = request.get_json()
        if not json_data:
            return error(status=400, detail="JSON data required")

        cloud_project = (json_data.get("cloud_project") or "").strip()
        if not cloud_project:
            return error(status=400, detail="cloud_project is required")
        if not _GCP_PROJECT_ID_RE.match(cloud_project):
            return error(
                status=400,
                detail=(
                    "cloud_project must be a valid GCP project ID: 6-30 characters, "
                    "lowercase letters, digits, and hyphens only, starting with a "
                    "letter and not ending with a hyphen."
                ),
            )

        # ----------------------------------------------------------------
        # Resolve numeric project number (needed for GCS IAM grant).
        # ----------------------------------------------------------------
        project_number: int | None = None
        gcs_write_access = False
        gcs_write_detail: str | None = None

        # Caller may supply project_number directly (manual-entry UX path used
        # when the user's OAuth token lacks the cloudplatformprojects.readonly
        # scope).  We still attempt a CRM lookup to verify ownership: if CRM
        # returns 200 the supplied number must match; only when CRM 403s (scope
        # genuinely absent) do we fall back to trusting the supplied value.
        raw_number = json_data.get("project_number")
        manually_supplied_number: int | None = None
        if raw_number is not None:
            try:
                manually_supplied_number = int(raw_number)
            except (TypeError, ValueError):
                return error(
                    status=400,
                    detail="project_number must be an integer.",
                )
            if not (
                _GCP_PROJECT_NUMBER_MIN
                <= manually_supplied_number
                <= _GCP_PROJECT_NUMBER_MAX
            ):
                return error(
                    status=400,
                    detail=(
                        "project_number is not in the valid range for a GCP project."
                    ),
                )

        # Always attempt CRM lookup to verify the user can access the project.
        access_token, refresh_token, _ = user.get_gee_oauth_credentials()
        if access_token and refresh_token:
            from google.auth.exceptions import RefreshError as _RefreshError
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.credentials import Credentials as _Credentials

            env_settings = SETTINGS.get("environment", {})
            credentials = _Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri=env_settings.get(
                    "GOOGLE_OAUTH_TOKEN_URI",
                    "https://oauth2.googleapis.com/token",
                ),
                client_id=env_settings.get("GOOGLE_OAUTH_CLIENT_ID"),
                client_secret=env_settings.get("GOOGLE_OAUTH_CLIENT_SECRET"),
            )
            try:
                session = AuthorizedSession(credentials)
                crm_resp = session.get(
                    f"https://cloudresourcemanager.googleapis.com/v3/projects/{cloud_project}",
                    timeout=15,
                )
                if crm_resp.status_code == 200:
                    crm_data = crm_resp.json()
                    raw = crm_data.get("projectNumber") or crm_data.get("name", "")
                    # CRM v3 returns projectNumber as string; name is
                    # "projects/123456789012".
                    if isinstance(raw, str) and raw.startswith("projects/"):
                        raw = raw.split("/")[-1]
                    try:
                        crm_number = int(raw)
                    except (TypeError, ValueError):
                        logger.warning(
                            "CRM response did not contain a parseable project "
                            "number for project '%s': %s",
                            cloud_project,
                            crm_data,
                        )
                        crm_number = None

                    if crm_number is not None:
                        # If the caller supplied a project_number, it must match
                        # what CRM returned — reject mismatches to prevent a user
                        # from claiming a project number they don't own.
                        if (
                            manually_supplied_number is not None
                            and manually_supplied_number != crm_number
                        ):
                            return error(
                                status=400,
                                detail=(
                                    "project_number does not match the project "
                                    f"'{cloud_project}'. Verify the number in "
                                    "the GCP Console."
                                ),
                            )
                        project_number = crm_number

                elif crm_resp.status_code == 404:
                    return error(
                        status=400,
                        detail=(
                            f"Project '{cloud_project}' not found. "
                            "Ensure the Earth Engine API is enabled in your "
                            "GCP project."
                        ),
                    )
                elif crm_resp.status_code == 403:
                    # Scope missing — fall back to the manually supplied number
                    # if one was provided; otherwise skip IAM grant entirely.
                    logger.info(
                        "CRM returned 403 for user %s (missing "
                        "cloudplatformprojects.readonly scope); "
                        "falling back to manually supplied project_number=%s.",
                        mask_email(user.email),
                        manually_supplied_number,
                    )
                    if manually_supplied_number is not None:
                        project_number = manually_supplied_number
                    else:
                        gcs_write_detail = (
                            "Project saved, but automatic bucket write access could "
                            "not be configured because your Google account connection "
                            "lacks the required scope. Re-connect your GEE account or "
                            "enter your Project Number manually to enable it."
                        )
                else:
                    logger.warning(
                        "CRM lookup for project '%s' returned %d for user %s: %s",
                        cloud_project,
                        crm_resp.status_code,
                        mask_email(user.email),
                        crm_resp.text[:200],
                    )
            except _RefreshError:
                logger.warning(
                    "OAuth token refresh failed during CRM lookup for user %s",
                    mask_email(user.email),
                )

        # ----------------------------------------------------------------
        # Perform the GCS IAM grant when we have a project number.
        # ----------------------------------------------------------------
        if project_number is not None:
            from gefapi.services.gcs_iam_service import (
                grant_gee_service_agent_bucket_write,
                revoke_gee_service_agent_bucket_write,
            )

            bucket_name = SETTINGS.get("GCS_OUTPUT_BUCKET", "ldmt")

            # Revoke the previous project's grant before issuing a new one so
            # stale service-agent bindings don't accumulate on the bucket.
            old_number = user.gee_cloud_project_number
            if old_number is not None and old_number != project_number:
                revoke_gee_service_agent_bucket_write(old_number, bucket_name)

            try:
                grant_gee_service_agent_bucket_write(project_number, bucket_name)
                gcs_write_access = True
            except Exception as iam_exc:
                logger.warning(
                    "GCS IAM grant failed for project %s (user %s): %s",
                    project_number,
                    mask_email(user.email),
                    iam_exc,
                )
                gcs_write_detail = (
                    "Project saved, but the bucket write permission could not be "
                    "configured automatically. Ensure the Earth Engine API is "
                    "enabled in your GCP project, then save the project again."
                )

        # ----------------------------------------------------------------
        # Persist.
        # ----------------------------------------------------------------
        user.gee_cloud_project = cloud_project
        user.gee_cloud_project_number = project_number
        db.session.commit()

        masked = mask_email(user.email)
        logger.info(
            "GEE cloud project set to '%s' (number=%s, gcs_write_access=%s) "
            "for user %s",
            cloud_project,
            project_number,
            gcs_write_access,
            masked,
        )

        response: dict = {
            "message": "GEE cloud project saved successfully",
            "gcs_write_access": gcs_write_access,
        }
        if gcs_write_detail:
            response["detail"] = gcs_write_detail
        return jsonify(response)

    except Exception as e:
        logger.error(f"Error setting GEE cloud project: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save GEE cloud project")


@endpoints.route("/user/me/gee-service-account", strict_slashes=False, methods=["POST"])
@jwt_required()
@require_scope("gee:write")
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

        logger.info(
            f"Successfully stored GEE service account for user {mask_email(user.email)}"
        )

        return jsonify(
            {"message": "GEE service account credentials saved successfully"}
        )

    except Exception as e:
        logger.error(f"Error uploading service account: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to save service account credentials")


@endpoints.route("/user/me/gee-credentials", strict_slashes=False, methods=["DELETE"])
@jwt_required()
@require_scope("gee:write")
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

        # Revoke the GCS IAM binding before clearing credentials so we can
        # still read the project number from the user object.
        if user.gee_cloud_project_number is not None:
            from gefapi.services.gcs_iam_service import (
                revoke_gee_service_agent_bucket_write,
            )

            bucket_name = SETTINGS.get("GCS_OUTPUT_BUCKET", "ldmt")
            revoke_gee_service_agent_bucket_write(
                user.gee_cloud_project_number, bucket_name
            )

        # Clear credentials
        user.clear_gee_credentials()
        db.session.commit()

        logger.info(
            f"Successfully deleted GEE credentials for user {mask_email(user.email)}"
        )

        return jsonify({"message": "GEE credentials deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting GEE credentials: {e}")
        db.session.rollback()
        return error(status=500, detail="Failed to delete GEE credentials")


@endpoints.route(
    "/user/me/gee-credentials/test", strict_slashes=False, methods=["POST"]
)
@jwt_required()
@require_scope("gee:read")
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
@require_scope("gee:read")
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
@require_scope("gee:write")
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
@require_scope("gee:write")
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
@require_scope("gee:read")
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
