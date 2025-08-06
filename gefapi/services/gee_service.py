"""Google Earth Engine Service for task management"""

import json
import logging
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
                        logger.debug(f"Found GEE task ID: {match}")

        return list(task_ids)

    @staticmethod
    def get_gee_service_account_token() -> Optional[str]:
        """
        Get an access token for the Google Earth Engine service account.

        Returns:
            Access token string if successful, None otherwise
        """
        try:
            # Try to get service account JSON from environment
            service_account_json = SETTINGS.get("environment", {}).get(
                "EE_SERVICE_ACCOUNT_JSON"
            )
            if not service_account_json:
                logger.warning(
                    "EE_SERVICE_ACCOUNT_JSON not configured, cannot get GEE "
                    "access token"
                )
                return None

            try:
                service_account_data = json.loads(service_account_json)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse EE_SERVICE_ACCOUNT_JSON: {e}")
                return None

            # Use Google's OAuth 2.0 service account flow
            try:
                import google.auth.transport.requests
                import google.oauth2.service_account
            except ImportError:
                logger.warning(
                    "Google auth libraries not available for GEE token generation"
                )
                return None

            # Create credentials from service account info
            credentials = (
                google.oauth2.service_account.Credentials.from_service_account_info(
                    service_account_data,
                    scopes=["https://www.googleapis.com/auth/earthengine"],
                )
            )

            # Refresh the token
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)

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
            logger.warning(
                "Could not get GEE access token, skipping task cancellations"
            )
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
