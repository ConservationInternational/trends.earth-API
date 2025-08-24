"""Google Earth Engine Service for task management"""

import contextlib
import json
import logging
import os
import re
import tempfile
from typing import Any

import rollbar

from gefapi.config import SETTINGS

logger = logging.getLogger(__name__)


class GEEService:
    """Service for managing Google Earth Engine tasks"""

    @staticmethod
    def _initialize_ee() -> bool:
        """
        Initialize Google Earth Engine with service account credentials.

        Returns:
            True if initialization successful, False otherwise
        """
        try:
            import ee  # type: ignore
        except ImportError as e:
            logger.error(f"Google Earth Engine API not available: {e}")
            return False

        try:
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
                logger.info("Successfully initialized Google Earth Engine")
                return True
            finally:
                # Clean up the temporary file
                with contextlib.suppress(Exception):
                    os.unlink(temp_key_path)

        except Exception as e:
            logger.error(f"Failed to initialize Google Earth Engine: {e}")
            rollbar.report_exc_info()
            return False

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
    def cancel_gee_task(task_id: str) -> dict[str, Any]:
        """
        Cancel a Google Earth Engine task using the Earth Engine API.

        Args:
            task_id: The GEE task ID to cancel

        Returns:
            Dictionary with cancellation result information
        """
        result = {"task_id": task_id, "success": False, "error": None, "status": None}

        try:
            # Initialize Earth Engine
            if not GEEService._initialize_ee():
                result["error"] = "Failed to initialize Google Earth Engine"
                return result

            import ee  # type: ignore

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
                            (
                                "Checking GEE task status for %s using operation '%s'"
                            ),
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
                    (
                        "Attempting to cancel GEE task %s using operation '%s'"
                    ),
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

        # Cancel each task
        results = []
        for task_id in task_ids:
            result = GEEService.cancel_gee_task(task_id)
            results.append(result)

        return results
