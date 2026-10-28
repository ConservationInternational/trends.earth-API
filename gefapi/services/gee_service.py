"""Google Earth Engine Service for task management"""

import contextlib
import json
import logging
import os
import re
import tempfile
from typing import Any
from urllib.parse import quote, urlparse

import rollbar

from gefapi.config import SETTINGS
from gefapi.utils import mask_email

logger = logging.getLogger(__name__)

_GOOGLE_SERVICE_ACCOUNT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GOOGLE_SERVICE_ACCOUNT_TOKEN_URIS = frozenset(
    {
        _GOOGLE_SERVICE_ACCOUNT_TOKEN_URI,
        "https://accounts.google.com/o/oauth2/token",
        "https://www.googleapis.com/oauth2/v4/token",
    }
)


def _service_account_credentials(service_account_data):
    """Build credentials whose token refresh cannot follow HTTP redirects."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    import requests

    class PinnedServiceAccountCredentials(service_account.Credentials):
        def refresh(self, request):
            # EE may supply either a requests or httplib2 transport. Use our own
            # transport so redirect protection is independent of that choice.
            with requests.Session() as session:
                token_request = Request(session=session)

                def trusted_request(url, **kwargs):
                    if url != _GOOGLE_SERVICE_ACCOUNT_TOKEN_URI:
                        raise RefreshError(
                            "Service account refresh requires the standard "
                            "Google HTTPS token endpoint."
                        )
                    kwargs["allow_redirects"] = False
                    response = token_request(url, **kwargs)
                    if 300 <= response.status < 400:
                        raise RefreshError(
                            "Google service account token endpoint returned a "
                            "redirect; redirects are not permitted."
                        )
                    return response

                return super().refresh(trusted_request)

    return PinnedServiceAccountCredentials.from_service_account_info(
        service_account_data, scopes=ee.oauth.SCOPES
    )


# Import ee at module level for test mocking, but allow ImportError
try:
    import ee  # type: ignore
except ImportError:
    ee = None

# Import google.oauth2 at module level for test mocking, but allow ImportError
try:
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
except ImportError:
    Credentials = None  # type: ignore
    RefreshError = Exception  # type: ignore


class GEEService:
    """Service for managing Google Earth Engine tasks"""

    @staticmethod
    def _oauth_token_uri() -> str:
        """Return the configured OAuth token URI with sane defaults."""

        env_settings = SETTINGS.get("environment") or {}
        token_uri = (
            env_settings.get("GOOGLE_OAUTH_TOKEN_URI")
            or os.getenv("GOOGLE_OAUTH_TOKEN_URI")
            or "https://oauth2.googleapis.com/token"
        )

        parsed = urlparse(token_uri)
        if not parsed.scheme or not parsed.netloc:
            logger.warning(
                "Invalid GOOGLE_OAUTH_TOKEN_URI '%s'; falling back to default",
                token_uri,
            )
            return "https://oauth2.googleapis.com/token"
        return token_uri

    @staticmethod
    def _initialize_ee(user=None) -> bool:
        """
        Initialize Google Earth Engine with service account credentials or
        user credentials.

        Args:
            user: Optional User model instance to use user-specific credentials

        Returns:
            True if initialization successful, False otherwise
        """
        try:
            if ee is None:
                logger.error("Google Earth Engine API not available")
                return False
            # Stored keys may predate upload validation. Reject unsafe keys even
            # when process-global Earth Engine credentials are already initialized.
            if (
                user
                and user.has_gee_credentials()
                and user.gee_credentials_type == "service_account"
                and not GEEService.validate_service_account_key(
                    user.get_gee_service_account()
                )
            ):
                return False
            # Check if already initialized by making a simple API call
            try:
                # Test with a simple API call
                ee.data.listOperations({"pageSize": 1})  # type: ignore
                logger.debug("Earth Engine already initialized")
                return True
            except Exception as e:
                # Not initialized, proceed with initialization
                logger.debug(
                    f"Earth Engine not initialized, proceeding with initialization: {e}"
                )

            # Try user credentials first if provided
            if user and user.has_gee_credentials():
                logger.info(
                    f"Attempting to initialize GEE with user {user.email} credentials"
                )

                if user.gee_credentials_type == "oauth":
                    if GEEService._initialize_ee_with_oauth(user):
                        logger.info(
                            f"Successfully initialized GEE with OAuth for "
                            f"user {user.email}"
                        )
                        return True
                    logger.warning(
                        f"Failed to initialize GEE with OAuth for user {user.email}, "
                        "falling back to service account"
                    )

                elif user.gee_credentials_type == "service_account":
                    if GEEService._initialize_ee_with_user_service_account(user):
                        logger.info(
                            f"Successfully initialized GEE with service account for "
                            f"user {user.email}"
                        )
                        return True
                    logger.warning(
                        f"Failed to initialize GEE with user service account for "
                        f"user {user.email}, falling back to default"
                    )

            # Fall back to default service account
            return GEEService._initialize_ee_with_default_service_account()

        except Exception as e:
            logger.error(f"Failed to initialize Google Earth Engine: {e}")
            rollbar.report_exc_info()
            return False

    @staticmethod
    def _initialize_ee_with_default_service_account() -> bool:
        """Initialize Google Earth Engine with the default service account"""
        try:
            if ee is None:
                logger.error("Google Earth Engine API not available")
                return False

            # Get service account JSON from environment
            service_account_json = SETTINGS.get("environment", {}).get(
                "EE_SERVICE_ACCOUNT_JSON"
            ) or os.getenv("EE_SERVICE_ACCOUNT_JSON")

            if not service_account_json:
                logger.error(
                    "EE_SERVICE_ACCOUNT_JSON not configured in environment, "
                    "cannot initialize Earth Engine"
                )
                return False

            # Handle both base64 encoded and direct JSON
            try:
                is_base64_encoded = False
                if not service_account_json.strip().startswith(("{", "[")):
                    import base64

                    test_decode = base64.b64decode(service_account_json).decode("utf-8")
                    if test_decode.strip().startswith(("{", "[")):
                        is_base64_encoded = True

                if is_base64_encoded:
                    import base64

                    decoded_json = base64.b64decode(service_account_json).decode(
                        "utf-8"
                    )
                    service_account_data = json.loads(decoded_json)
                else:
                    service_account_data = json.loads(service_account_json)

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse EE_SERVICE_ACCOUNT_JSON: {e}")
                return False

            # Write service account data to a temporary file for Earth Engine
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as temp_file:
                json.dump(service_account_data, temp_file)
                temp_key_path = temp_file.name

            try:
                # Initialize Earth Engine with service account
                service_account_email = service_account_data["client_email"]
                credentials = ee.ServiceAccountCredentials(  # type: ignore[attr-defined]
                    service_account_email, temp_key_path
                )  # type: ignore
                ee.Initialize(credentials)  # type: ignore
                logger.info(
                    "Successfully initialized Google Earth Engine with "
                    "default service account"
                )
                return True
            finally:
                # Clean up the temporary file
                with contextlib.suppress(Exception):
                    os.unlink(temp_key_path)

        except Exception as e:
            logger.error(
                f"Failed to initialize Google Earth Engine with "
                f"default service account: {e}"
            )
            return False

    @staticmethod
    def _initialize_ee_with_oauth(user) -> bool:
        """Initialize Google Earth Engine with user OAuth credentials"""
        try:
            if ee is None:
                logger.error("Google Earth Engine API not available")
                return False

            access_token, refresh_token, cloud_project = (
                user.get_gee_oauth_credentials()
            )
            if not access_token or not refresh_token:
                logger.warning(
                    f"Missing OAuth tokens for user {mask_email(user.email)}"
                )
                return False

            if not cloud_project:
                logger.error(
                    f"No GEE cloud project set for user {mask_email(user.email)}. "
                    "Please select a GCP project in the app after connecting GEE."
                )
                return False

            # Create OAuth credentials
            credentials = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri=GEEService._oauth_token_uri(),
                client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
            )

            # Try to refresh token if needed.
            # credentials.expired is only True when an expiry timestamp was set;
            # we don't persist expiry, so also refresh when expiry is unknown.
            try:
                needs_refresh = credentials.expired or credentials.expiry is None
                if needs_refresh and credentials.refresh_token:
                    logger.info(
                        f"Refreshing OAuth token for user {mask_email(user.email)}"
                    )
                    from google.auth.transport.requests import Request

                    credentials.refresh(Request())
                    # Update stored tokens (cloud_project preserved — not overwritten)
                    user.set_gee_oauth_credentials(
                        credentials.token, credentials.refresh_token
                    )
                    from gefapi import db

                    db.session.commit()
            except RefreshError as e:
                masked = mask_email(user.email)
                logger.error(f"Failed to refresh OAuth token for user {masked}: {e}")
                return False

            # Initialize Earth Engine with the user's own GCP project so that
            # EE API calls are billed to their project, not the server's.
            ee.Initialize(credentials, project=cloud_project)  # type: ignore
            logger.info(
                f"Successfully initialized Google Earth Engine with OAuth for "
                f"user {mask_email(user.email)}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to initialize Google Earth Engine with OAuth for "
                f"user {mask_email(user.email)}: {e}"
            )
            return False

    @staticmethod
    def _initialize_ee_with_user_service_account(user) -> bool:
        """Initialize Google Earth Engine with user service account"""
        try:
            if ee is None:
                logger.error("Google Earth Engine API not available")
                return False

            service_account_data = user.get_gee_service_account()
            if not service_account_data:
                logger.warning(
                    f"No service account data for user {mask_email(user.email)}"
                )
                return False

            service_account_data = GEEService.sanitize_service_account_key(
                service_account_data
            )
            credentials = _service_account_credentials(service_account_data)
            ee.Initialize(credentials)  # type: ignore
            logger.info(
                f"Successfully initialized Google Earth Engine with "
                f"service account for user {user.email}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to initialize Google Earth Engine with user "
                f"service account for user {user.email}: {e}"
            )
            return False

    @staticmethod
    def validate_service_account_key(service_account_data: dict[str, Any]) -> bool:
        """Validate a service account key structure and basic functionality"""
        try:
            GEEService.sanitize_service_account_key(service_account_data)
            return True
        except Exception as e:
            logger.error(f"Error validating service account key: {e}")
            return False

    @staticmethod
    def sanitize_service_account_key(
        service_account_data: dict[str, Any],
    ) -> dict[str, str]:
        """Return a validated copy suitable for API or workload credentials.

        Call before serializing stored user keys into EE_SERVICE_ACCOUNT_JSON;
        loading a stored document does not imply it passed upload validation.
        The result pins Google's token endpoint and universe and excludes
        optional fields that might influence credential loading.

        Raises:
            TypeError: If the document is not a JSON object.
            ValueError: If its schema, universe, or endpoints are unsupported.
        """
        return GEEService._validated_service_account_key(service_account_data)

    @staticmethod
    def _validated_service_account_key(
        service_account_data: dict[str, Any],
    ) -> dict[str, str]:
        """Validate uploaded or stored keys and pin their effective token URI."""
        if not isinstance(service_account_data, dict):
            raise TypeError("Service account key must be a JSON object.")

        required_fields = {
            "type",
            "project_id",
            "private_key_id",
            "private_key",
            "client_email",
            "client_id",
            "auth_uri",
            "token_uri",
        }
        optional_fields = {
            "auth_provider_x509_cert_url",
            "client_x509_cert_url",
            "universe_domain",
        }
        missing_fields = required_fields - service_account_data.keys()
        if missing_fields:
            raise ValueError(
                f"Service account key missing required fields: {sorted(missing_fields)}"
            )
        if service_account_data.keys() - required_fields - optional_fields:
            raise ValueError(
                "Unsupported service account fields. Upload a standard Google "
                "service account JSON key, not external or impersonated credentials."
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in service_account_data.values()
        ):
            raise ValueError("Service account key fields must be nonempty strings.")
        if service_account_data["type"] != "service_account":
            raise ValueError("Credential type must be service_account.")
        client_email = service_account_data["client_email"]
        if not re.fullmatch(r"[^@\s/]+@[^@\s/]+", client_email):
            raise ValueError("Invalid service account client email format.")
        if not service_account_data["private_key"].startswith("-----BEGIN"):
            raise ValueError("Invalid service account private key format.")
        if service_account_data.get("universe_domain", "googleapis.com") != (
            "googleapis.com"
        ):
            raise ValueError(
                "Unsupported service account universe_domain; only googleapis.com "
                "is supported."
            )
        if service_account_data["token_uri"] not in _GOOGLE_SERVICE_ACCOUNT_TOKEN_URIS:
            raise ValueError(
                "Unsupported service account token_uri; use "
                "https://oauth2.googleapis.com/token."
            )
        if service_account_data["auth_uri"] != (
            "https://accounts.google.com/o/oauth2/auth"
        ):
            raise ValueError(
                "Unsupported service account auth_uri; use "
                "https://accounts.google.com/o/oauth2/auth."
            )
        if (
            service_account_data.get(
                "auth_provider_x509_cert_url",
                "https://www.googleapis.com/oauth2/v1/certs",
            )
            != "https://www.googleapis.com/oauth2/v1/certs"
        ):
            raise ValueError("Unsupported service account auth_provider_x509_cert_url.")
        cert_prefix = "https://www.googleapis.com/robot/v1/metadata/x509/"
        if "client_x509_cert_url" in service_account_data and service_account_data[
            "client_x509_cert_url"
        ] not in {
            cert_prefix + quote(client_email, safe=""),
            cert_prefix + quote(client_email, safe="@"),
        }:
            raise ValueError(
                "Unsupported service account client_x509_cert_url; use the "
                "standard Google certificate URL for this client_email."
            )

        # Do not let optional uploaded fields influence google-auth behavior.
        return {
            **{field: service_account_data[field] for field in required_fields},
            "token_uri": _GOOGLE_SERVICE_ACCOUNT_TOKEN_URI,
            "universe_domain": "googleapis.com",
        }

    @staticmethod
    def extract_gee_task_ids_from_logs(execution_logs: list[str]) -> list[str]:
        """
        Extract Google Earth Engine task IDs from execution logs.

        Looks for patterns like:
        - "Starting GEE task 6CIGR7EG2J45GJ2DN2J7X3WZ"
        - "Backing off ... for task YBKKBHM2V63JYBVIPCCRY7A2"

        Args:
            execution_logs: List of log text entries

        Returns:
            List of unique GEE task IDs found in the logs
        """
        task_ids = set()

        # Regex patterns to match GEE task IDs in various log formats
        patterns = [
            r"Starting GEE task ([A-Z0-9]{24})",
            r"for task ([A-Z0-9]{24})",
            r"task.*?([A-Z0-9]{24})",
            r"GEE.*?task.*?([A-Z0-9]{24})",
        ]

        for log_text in execution_logs:
            if not log_text:
                continue

            for pattern in patterns:
                matches = re.findall(pattern, log_text)
                for match in matches:
                    # Validate that the match looks like a valid GEE task ID
                    if len(match) == 24 and match.isalnum() and match.isupper():
                        task_ids.add(match)

        return list(task_ids)

    @staticmethod
    def cancel_gee_task(task_id: str, user=None) -> dict[str, Any]:
        """
        Cancel a Google Earth Engine task using the Earth Engine API.

        Args:
            task_id: The GEE task ID to cancel
            user: Optional User model instance to use user-specific credentials

        Returns:
            Dictionary with cancellation result information
        """
        result = {"task_id": task_id, "success": False, "error": None, "status": None}

        try:
            # Initialize Earth Engine
            if not GEEService._initialize_ee(user):
                result["error"] = "Failed to initialize Google Earth Engine"
                return result

            if ee is None:
                result["error"] = "Google Earth Engine API not available"
                return result

            # Get the project ID (prefer config, fall back to environment)
            project_id = SETTINGS.get("environment", {}).get(
                "GOOGLE_PROJECT_ID"
            ) or os.getenv("GOOGLE_PROJECT_ID")

            # Build candidate operation names
            operation_candidates = []
            if project_id:
                operation_candidates.append(
                    f"projects/{project_id}/operations/{task_id}"
                )
            else:
                logger.warning(
                    "GOOGLE_PROJECT_ID not set; attempting cancellation without "
                    "project prefix"
                )
            # Always include a fallback to the unqualified operation path
            operation_candidates.append(f"operations/{task_id}")

            # We'll use the first operation name that responds without raising
            operation_name = None

            try:
                # Get task status first
                # Try candidates until one succeeds
                last_error = None
                task_info = None
                for candidate in operation_candidates:
                    try:
                        logger.info(
                            ("Checking GEE task status for %s using operation '%s'"),
                            task_id,
                            candidate,
                        )
                        task_info = ee.data.getOperation(candidate)  # type: ignore
                        operation_name = candidate
                        break
                    except Exception as e:
                        last_error = e
                        logger.debug(
                            (
                                "Operation check failed for '%s': %s. "
                                "Trying next candidate..."
                            ),
                            candidate,
                            e,
                        )
                        continue

                if task_info is None:
                    raise last_error or Exception(
                        "Failed to query task status with available operation names"
                    )

                if task_info:
                    # Extract status from the operation
                    if "done" in task_info:
                        if task_info["done"]:
                            result["status"] = "COMPLETED"
                            if "error" in task_info:
                                result["status"] = "FAILED"
                            else:
                                result["status"] = "SUCCEEDED"
                        else:
                            result["status"] = "RUNNING"

                    logger.info(
                        f"GEE task {task_id} current status: "
                        f"{result['status']} in project {project_id}"
                    )

                    # If already completed, no need to cancel
                    if result["status"] in ["SUCCEEDED", "FAILED"]:
                        result["success"] = True
                        result["error"] = f"Task already in {result['status']} state"
                        return result

                # Attempt to cancel the task
                logger.info(
                    ("Attempting to cancel GEE task %s using operation '%s'"),
                    task_id,
                    operation_name,
                )
                # If we haven't set a working operation name yet, try candidates now
                if not operation_name:
                    for candidate in operation_candidates:
                        try:
                            ee.data.cancelOperation(candidate)  # type: ignore
                            operation_name = candidate
                            break
                        except Exception as e:
                            logger.debug(
                                (
                                    "Cancel attempt failed for '%s': %s. Trying next "
                                    "candidate..."
                                ),
                                candidate,
                                e,
                            )
                            continue

                    if not operation_name:
                        raise Exception(
                            "Failed to cancel task with available operation names"
                        )
                else:
                    ee.data.cancelOperation(operation_name)  # type: ignore

                result["success"] = True
                result["status"] = "CANCELLED"
                logger.info(f"Successfully cancelled GEE task {task_id}")

            except Exception as api_error:
                error_msg = str(api_error)

                # Handle specific error cases
                if "not found" in error_msg.lower() or "404" in error_msg:
                    result["error"] = "Task not found"
                    logger.warning(f"GEE task {task_id} not found")
                elif "permission" in error_msg.lower() or "403" in error_msg:
                    result["error"] = f"Permission denied: {error_msg}"
                    logger.error(
                        f"Permission denied for GEE task {task_id}: {error_msg}"
                    )
                elif "already" in error_msg.lower() and (
                    "completed" in error_msg.lower() or "finished" in error_msg.lower()
                ):
                    result["error"] = (
                        "Task cannot be cancelled (may already be completed)"
                    )
                    result["success"] = True  # Not really an error if already completed
                    logger.warning(
                        f"GEE task {task_id} cannot be cancelled: {error_msg}"
                    )
                else:
                    result["error"] = f"API error: {error_msg}"
                    logger.error(f"Failed to cancel GEE task {task_id}: {error_msg}")

        except Exception as e:
            result["error"] = f"Unexpected error: {e!s}"
            logger.error(f"Unexpected error cancelling GEE task {task_id}: {e}")
            rollbar.report_exc_info()

        return result

    @staticmethod
    def cancel_gee_tasks_from_execution(
        execution_logs: list[str], user=None
    ) -> list[dict[str, Any]]:
        """
        Extract GEE task IDs from execution logs and attempt to cancel them.

        Args:
            execution_logs: List of log text entries from the execution
            user: Optional User model instance to use user-specific credentials

        Returns:
            List of cancellation results for each task found
        """
        logger.info("Scanning execution logs for GEE task IDs to cancel")

        # Extract task IDs from logs
        task_ids = GEEService.extract_gee_task_ids_from_logs(execution_logs)

        if not task_ids:
            logger.info("No GEE task IDs found in execution logs")
            return []

        logger.info(f"Found {len(task_ids)} GEE task IDs to cancel: {task_ids}")

        # Cancel each task
        results = []
        for task_id in task_ids:
            result = GEEService.cancel_gee_task(task_id, user)
            results.append(result)

        return results
