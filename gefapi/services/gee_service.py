"""Google Earth Engine Service for task management"""

import json
import logging
import os
import re
from typing import Any, Optional

import requests
import rollbar

from gefapi.config import SETTINGS

logger = logging.getLogger(__name__)


class GEEService:
    """Service for managing Google Earth Engine tasks"""

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
    def diagnose_gee_token_issue() -> dict[str, Any]:
        """
        Diagnose common issues with GEE token generation.

        Returns:
            Dictionary with diagnostic information
        """
        diagnosis = {
            "environment_variable_present": False,
            "settings_configured": False,
            "json_parseable": False,
            "required_fields_present": False,
            "google_libs_available": False,
            "issues": [],
            "recommendations": [],
        }

        # Check environment variable
        service_account_json = SETTINGS.get("environment", {}).get(
            "EE_SERVICE_ACCOUNT_JSON"
        ) or os.getenv("EE_SERVICE_ACCOUNT_JSON")

        if not service_account_json:
            diagnosis["issues"].append(
                "EE_SERVICE_ACCOUNT_JSON not found in environment or settings"
            )
            diagnosis["recommendations"].append(
                "Set EE_SERVICE_ACCOUNT_JSON environment variable with "
                "base64-encoded service account JSON"
            )
            return diagnosis

        diagnosis["environment_variable_present"] = True
        diagnosis["settings_configured"] = bool(
            SETTINGS.get("environment", {}).get("EE_SERVICE_ACCOUNT_JSON")
        )

        # Check JSON parsing
        try:
            # Use the same robust base64 detection as the main method
            is_base64_encoded = False
            try:
                if not service_account_json.strip().startswith(("{", "[")):
                    import base64

                    test_decode = base64.b64decode(service_account_json).decode("utf-8")
                    if test_decode.strip().startswith(("{", "[")):
                        is_base64_encoded = True
            except (ValueError, TypeError):
                # If base64 decoding fails, treat as direct JSON
                pass

            if is_base64_encoded:
                import base64

                decoded_json = base64.b64decode(service_account_json).decode("utf-8")
                service_account_data = json.loads(decoded_json)
            else:
                service_account_data = json.loads(service_account_json)

            diagnosis["json_parseable"] = True

            # Check required fields
            required_fields = ["type", "project_id", "private_key", "client_email"]
            missing_fields = [
                field for field in required_fields if field not in service_account_data
            ]

            if missing_fields:
                diagnosis["issues"].append(
                    f"Service account JSON missing required fields: {missing_fields}"
                )
                diagnosis["recommendations"].append(
                    "Ensure service account JSON contains all required fields"
                )
            else:
                diagnosis["required_fields_present"] = True

        except Exception as e:
            diagnosis["issues"].append(f"Failed to parse service account JSON: {e}")
            diagnosis["recommendations"].append(
                "Check that EE_SERVICE_ACCOUNT_JSON is valid base64-encoded JSON"
            )

        # Check Google auth libraries
        try:
            import importlib.util

            if importlib.util.find_spec(
                "google.auth.transport.requests"
            ) and importlib.util.find_spec("google.oauth2.service_account"):
                diagnosis["google_libs_available"] = True
            else:
                raise ImportError("Required Google auth modules not found")
        except ImportError as e:
            diagnosis["issues"].append(f"Google auth libraries not available: {e}")
            diagnosis["recommendations"].append(
                "Ensure google-auth and google-auth-oauthlib packages are installed"
            )

        return diagnosis

    @staticmethod
    def get_gee_service_account_token() -> Optional[str]:
        """
        Get an access token for the Google Earth Engine service account.

        Returns:
            Access token string if successful, None otherwise
        """
        try:
            # Try to get service account JSON from environment - check both locations
            service_account_json = SETTINGS.get("environment", {}).get(
                "EE_SERVICE_ACCOUNT_JSON"
            ) or os.getenv("EE_SERVICE_ACCOUNT_JSON")

            if not service_account_json:
                logger.error(
                    "EE_SERVICE_ACCOUNT_JSON not configured in environment or "
                    "settings, cannot get GEE access token. Check environment "
                    "variables."
                )
                return None

            try:
                # Handle both base64 encoded and direct JSON
                is_base64_encoded = False
                try:
                    # Check if it looks like base64 and doesn't start with '{' or '['
                    if not service_account_json.strip().startswith(("{", "[")):
                        # Try to decode as base64 to see if it's valid
                        import base64

                        test_decode = base64.b64decode(service_account_json).decode(
                            "utf-8"
                        )
                        # If decoded content looks like JSON, it's base64 encoded
                        if test_decode.strip().startswith(("{", "[")):
                            is_base64_encoded = True
                except (ValueError, TypeError, UnicodeDecodeError):
                    # If base64 decoding fails, treat as direct JSON
                    logger.debug("Service account data is not base64 encoded")

                if is_base64_encoded:
                    import base64

                    decoded_json = base64.b64decode(service_account_json).decode(
                        "utf-8"
                    )
                    service_account_data = json.loads(decoded_json)
                else:
                    service_account_data = json.loads(service_account_json)

                # Validate required fields
                required_fields = ["type", "project_id", "private_key", "client_email"]
                missing_fields = [
                    field
                    for field in required_fields
                    if field not in service_account_data
                ]
                if missing_fields:
                    logger.error(
                        f"Service account JSON missing required fields: "
                        f"{missing_fields}"
                    )
                    return None

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse EE_SERVICE_ACCOUNT_JSON: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error parsing service account JSON: {e}")
                return None

            # Use Google's OAuth 2.0 service account flow
            try:
                import google.auth.transport.requests
                import google.oauth2.service_account
            except ImportError as e:
                logger.error(
                    f"Google auth libraries not available for GEE token generation: {e}"
                )
                return None

            # Create credentials from service account info
            try:
                credentials = (
                    google.oauth2.service_account.Credentials.from_service_account_info(
                        service_account_data,
                        scopes=["https://www.googleapis.com/auth/earthengine"],
                    )
                )
            except Exception as e:
                logger.error(f"Failed to create service account credentials: {e}")
                rollbar.report_exc_info()
                return None

            # Refresh the token
            try:
                request = google.auth.transport.requests.Request()
                credentials.refresh(request)
            except Exception as e:
                logger.error(f"Failed to refresh service account token: {e}")
                rollbar.report_exc_info()
                return None

            logger.info("Successfully obtained GEE service account token")
            return credentials.token

        except Exception as e:
            logger.error(f"Failed to get GEE service account token: {e}")
            rollbar.report_exc_info()
            return None

    @staticmethod
    def cancel_gee_task(
        task_id: str, access_token: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Cancel a Google Earth Engine task using the REST API.

        Args:
            task_id: The GEE task ID to cancel
            access_token: Optional access token, will be generated if not provided

        Returns:
            Dictionary with cancellation result information
        """
        result = {"task_id": task_id, "success": False, "error": None, "status": None}

        try:
            # Get access token if not provided
            if not access_token:
                access_token = GEEService.get_gee_service_account_token()
                if not access_token:
                    result["error"] = "Failed to get GEE access token"
                    return result

            # Google Earth Engine REST API endpoint for task operations
            # The exact endpoint might need to be adjusted based on GEE API
            # documentation
            gee_endpoint = SETTINGS.get("environment", {}).get("GEE_ENDPOINT")
            if gee_endpoint:
                base_url = gee_endpoint
            else:
                base_url = "https://earthengine.googleapis.com"

            # Try to cancel the task
            project_id = SETTINGS.get("environment", {}).get(
                "GOOGLE_PROJECT_ID", "earthengine-legacy"
            )
            cancel_url = (
                f"https://earthengine.googleapis.com/v1/projects/"
                f"{project_id}/operations/{task_id}:cancel"
            )

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            # First try to get task status
            status_url = f"{base_url}/v1/projects/{project_id}/operations/{task_id}"

            logger.info(f"Checking GEE task status for {task_id}")
            status_response = requests.get(status_url, headers=headers, timeout=30)

            if status_response.status_code == 200:
                task_info = status_response.json()
                result["status"] = task_info.get("metadata", {}).get("state", "UNKNOWN")
                logger.info(f"GEE task {task_id} current status: {result['status']}")

                # If already completed, no need to cancel
                if result["status"] in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                    result["success"] = True
                    result["error"] = f"Task already in {result['status']} state"
                    return result
            else:
                logger.warning(
                    f"Could not get status for GEE task {task_id}: "
                    f"{status_response.status_code}"
                )

            # Attempt to cancel the task
            logger.info(f"Attempting to cancel GEE task {task_id}")
            cancel_response = requests.post(
                cancel_url, headers=headers, json={}, timeout=30
            )

            if cancel_response.status_code in [200, 204]:
                result["success"] = True
                result["status"] = "CANCELLED"
                logger.info(f"Successfully cancelled GEE task {task_id}")
            elif cancel_response.status_code == 404:
                result["error"] = "Task not found"
                logger.warning(f"GEE task {task_id} not found")
            elif cancel_response.status_code == 400:
                # Task might already be completed or in non-cancellable state
                result["error"] = "Task cannot be cancelled (may already be completed)"
                logger.warning(
                    f"GEE task {task_id} cannot be cancelled: {cancel_response.text}"
                )
            else:
                result["error"] = (
                    f"HTTP {cancel_response.status_code}: {cancel_response.text}"
                )
                logger.error(f"Failed to cancel GEE task {task_id}: {result['error']}")

        except requests.RequestException as e:
            result["error"] = f"Network error: {str(e)}"
            logger.error(f"Network error cancelling GEE task {task_id}: {e}")
        except Exception as e:
            result["error"] = f"Unexpected error: {str(e)}"
            logger.error(f"Unexpected error cancelling GEE task {task_id}: {e}")
            rollbar.report_exc_info()

        return result

    @staticmethod
    def cancel_gee_tasks_from_execution(
        execution_logs: list[str],
    ) -> list[dict[str, Any]]:
        """
        Extract GEE task IDs from execution logs and attempt to cancel them.

        Args:
            execution_logs: List of log text entries from the execution

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

        # Get access token once for all cancellations
        access_token = GEEService.get_gee_service_account_token()
        if not access_token:
            logger.error(
                "Could not get GEE access token, skipping task cancellations. "
                "Running diagnostics..."
            )

            # Run diagnostics to identify the issue
            try:
                diagnosis = GEEService.diagnose_gee_token_issue()
                if diagnosis["issues"]:
                    logger.error(
                        f"GEE token issues identified: {'; '.join(diagnosis['issues'])}"
                    )
                    logger.info(
                        f"Recommendations: {'; '.join(diagnosis['recommendations'])}"
                    )
                else:
                    logger.error(
                        "No specific issues identified in GEE token generation"
                    )
            except Exception as e:
                logger.error(f"Failed to run GEE token diagnostics: {e}")

            return [
                {
                    "task_id": task_id,
                    "success": False,
                    "error": "Could not get GEE access token",
                    "status": None,
                }
                for task_id in task_ids
            ]

        # Cancel each task
        results = []
        for task_id in task_ids:
            result = GEEService.cancel_gee_task(task_id, access_token)
            results.append(result)

        return results
