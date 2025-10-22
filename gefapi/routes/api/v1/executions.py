"""Execution management routes for the Trends.Earth API.

This module contains all endpoints related to script execution, execution monitoring,
logs retrieval, and execution lifecycle management.
"""

import logging

import dateutil.parser
from flask import Response, json, jsonify, request
from flask_jwt_extended import current_user, jwt_required

from gefapi import limiter
from gefapi.errors import ExecutionNotFound, ScriptNotFound, ScriptStateNotValid
from gefapi.routes.api.v1 import endpoints, error
from gefapi.services import ExecutionService
from gefapi.utils.permissions import can_access_admin_features, is_admin_or_higher
from gefapi.utils.rate_limiting import (
    RateLimitConfig,
    get_admin_aware_key,
    is_rate_limiting_disabled,
)
from gefapi.validators import validate_execution_log_creation, validate_execution_update

logger = logging.getLogger()


# SCRIPT EXECUTION
@endpoints.route("/script/<script>/run", strict_slashes=False, methods=["POST"])
@limiter.limit(
    lambda: ";".join(RateLimitConfig.get_execution_run_limits()),
    key_func=get_admin_aware_key,
    exempt_when=is_rate_limiting_disabled,
)  # Rate limit script execution
@jwt_required()
def run_script(script):
    """
    Execute a script with provided parameters.

    **Rate Limited**: Subject to execution limits (configurable per minute/hour)
    **Authentication**: JWT token required
    **Access**: Script must be published and user must have execution permissions

    **Request Schema**:
    ```json
    {
      "param1": "value1",
      "param2": 123,
      "param3": true,
      "nested_param": {
        "sub_param": "nested_value"
      }
    }
    ```

    **Path Parameters**:
    - `script`: Script identifier/name to execute

    **Request Body**: JSON object containing script parameters
    - Parameters vary by script - see individual script documentation
    - Can include nested objects and arrays
    - Boolean, string, and numeric values supported

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "exec_123456",
        "script_id": "my-script",
        "status": "PENDING",
        "params": {
          "param1": "value1",
          "param2": 123
        },
        "user_id": "user_789",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z",
        "start_time": null,
        "end_time": null,
        "results": null
      }
    }
    ```

    **Execution States**:
    - `PENDING`: Execution queued, waiting to start
    - `RUNNING`: Currently executing
    - `SUCCESS`: Completed successfully
    - `FAILED`: Execution failed with error
    - `CANCELLED`: Execution was cancelled

    **Error Responses**:
    - `400 Bad Request`: Invalid parameters or script not in valid state
    - `401 Unauthorized`: JWT token required
    - `404 Not Found`: Script does not exist
    - `429 Too Many Requests`: Execution rate limit exceeded
    - `500 Internal Server Error`: Execution creation failed
    """
    logger.info("[ROUTER]: Running script: " + script)
    user = current_user
    try:
        params = request.args.to_dict() if request.args else {}
        if request.get_json(silent=True):
            params.update(request.get_json())
        if "token" in params:
            del params["token"]
        execution = ExecutionService.create_execution(script, params, user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except ScriptStateNotValid as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=execution.serialize(user=current_user)), 200


@endpoints.route("/execution/user", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_user_executions():
    """
    Retrieve executions for the current authenticated user.

    **Authentication**: JWT token required
    **Access**: Returns only executions belonging to the current user
    **Scope**: User-specific endpoint - users can only see their own executions

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include in response
    - `exclude`: Comma-separated list of fields to exclude from response
    - `filter`: Search/filter executions by script name, status, or other attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-created_at',
      '-updated_at')
    - `updated_at`: Filter executions started after specific timestamp (ISO 8601)
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "exec-123",
          "script_id": "script-456",
          "status": "SUCCESS",
          "params": {
            "region": "africa",
            "year_start": 2020,
            "year_end": 2023
          },
          "user_id": "user-789",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T11:45:00Z",
          "start_time": "2025-01-15T10:31:00Z",
          "end_time": "2025-01-15T11:45:00Z",
          "results": {
            "output_file": "analysis_results.json",
            "summary": "Processing completed successfully"
          }
        }
      ]
    }
    ```

    **Response Schema (with pagination)**:
    ```json
    {
      "data": [...],
      "page": 1,
      "per_page": 20,
      "total": 45
    }
    ```

    **Execution Status Values**:
    - `PENDING`: Execution queued, waiting to start
    - `RUNNING`: Currently executing
    - `SUCCESS`: Completed successfully with results
    - `FAILED`: Execution failed with error
    - `CANCELLED`: Execution was cancelled before completion

    **Filtering Examples**:
    - `?filter=land-analysis` - Find executions related to "land-analysis" script
    - `?filter=status:SUCCESS` - Find only successful executions
    - `?filter=2024` - Find executions from 2024 (searches in timestamps)

    **Sorting Examples**:
    - `?sort=created_at` - Sort by creation time ascending
    - `?sort=-updated_at` - Sort by last update descending (most recent first)
    - `?sort=status` - Sort by execution status

    **Timestamp Filtering**:
    - `?updated_at=2025-01-15T10:30:00Z` - Find executions started after date
    - `updated_at` parameter accepts ISO 8601 format
    - Returns executions that started after the specified timestamp
    - Useful for incremental synchronization and monitoring recent activity

    **Pagination Examples**:
    - `?page=1&per_page=50` - Get first 50 executions with pagination
    - `?page=2&per_page=20` - Get second page with 20 executions per page
    - **Performance Note**: Without pagination, results are limited to 1000 executions

    **Field Control Examples**:
    - `?include=script,logs` - Include script details and execution logs
    - `?include=script_name,user_name` - Include script and user names
    - `?exclude=params,results` - Exclude verbose parameter and result data

    **Combined Query Examples**:
        - `?updated_at=2025-08-01&include=script&page=1&per_page=20`
    - `?filter=vegetation&sort=-created_at&exclude=params`

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve executions
    """
    logger.info(f"[ROUTER]: Getting executions for user: {current_user.email}")
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    sort = request.args.get("sort", None)

    # Add support for updated_at filtering
    updated_at = request.args.get("updated_at", None)
    if updated_at:
        updated_at = dateutil.parser.parse(updated_at)

    # Pagination parameters
    page_param = request.args.get("page", None)
    per_page_param = request.args.get("per_page", None)

    if page_param is not None or per_page_param is not None:
        try:
            page = int(page_param) if page_param is not None else 1
            per_page = int(per_page_param) if per_page_param is not None else 20
            page = max(page, 1)
            per_page = min(max(per_page, 1), 100)
            paginate = True
        except Exception:
            page, per_page = 1, 20
            paginate = True
    else:
        page, per_page = None, None
        paginate = False

    try:
        # Get executions for current user only
        executions, total = ExecutionService.get_executions(
            user=current_user,
            target_user_id=str(current_user.id),  # Force user filtering
            updated_at=updated_at,
            status=None,
            page=page,
            per_page=per_page,
            paginate=paginate,
            filter_param=filter_param,
            sort=sort,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    response_data = {
        "data": [
            execution.serialize(include, exclude, current_user)
            for execution in executions
        ]
    }

    # Only include pagination metadata if pagination was requested
    if paginate:
        response_data["page"] = page
        response_data["per_page"] = per_page
        response_data["total"] = total

    return jsonify(response_data), 200


@endpoints.route("/execution", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_executions():
    """
    Retrieve all executions with admin filtering and cross-user visibility.

    **Authentication**: JWT token required
    **Access**: Admin users can see all executions, regular users see only their own
    **Admin Features**: ADMIN+ users can filter by user_id to view other users'
      executions

    **Query Parameters**:
    - `user_id`: Filter executions by specific user ID (admin-only feature)
    - `updated_at`: Filter executions started after specific timestamp (ISO 8601)
    - `status`: Filter by execution status (PENDING, RUNNING, SUCCESS, FAILED,
      CANCELLED)
    - `include`: Comma-separated list of additional fields to include
    - `exclude`: Comma-separated list of fields to exclude
    - `filter`: General search/filter across execution attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-updated_at')
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20, max without pagination:
      varies by permission)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "exec-123",
          "script_id": "script-456",
          "status": "SUCCESS",
          "params": {
            "region": "africa",
            "analysis_type": "vegetation_change"
          },
          "user_id": "user-789",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T11:45:00Z",
          "start_time": "2025-01-15T10:31:00Z",
          "end_time": "2025-01-15T11:45:00Z",
          "results": {
            "analysis_complete": true,
            "output_files": ["vegetation_2023.tif", "change_summary.json"]
          }
        }
      ]
    }
    ```

    **Response Schema (with pagination)**:
    ```json
    {
      "data": [...],
      "page": 1,
      "per_page": 20,
      "total": 1250
    }
    ```

    **Admin Query Examples**:
    - `?user_id=123` - View executions for specific user (admin only)
    - `?status=FAILED` - Find all failed executions across system
    - `?updated_at=2025-01-15T00:00:00Z` - Find executions updated since date

    **Regular User Behavior**:
    - Non-admin users: Only see their own executions regardless of user_id parameter
    - Admin users: Can see all executions, can filter by user_id

    **Filtering Examples**:
    - `?filter=vegetation` - Find executions with "vegetation" in script or params
    - `?filter=2024-12` - Find executions from December 2024
    - `?status=RUNNING&sort=-created_at` - Find currently running executions,
      newest first

    **Sorting Examples**:
    - `?sort=updated_at` - Sort by last update ascending
    - `?sort=-created_at` - Sort by creation time descending (newest first)
    - `?sort=status` - Sort by execution status alphabetically

    **Timestamp Filtering**:
    - `updated_at` parameter accepts ISO 8601 format: `2025-01-15T10:30:00Z`
    - Returns executions that started after the specified timestamp
    - Useful for incremental synchronization and monitoring

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve executions
    """
    logger.info("[ROUTER]: Getting all executions: ")
    user_id = request.args.get("user_id", None)
    updated_at = request.args.get("updated_at", None)
    if updated_at:
        updated_at = dateutil.parser.parse(updated_at)
    status = request.args.get("status", None)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    # Pagination parameters - only paginate if user requests it
    page_param = request.args.get("page", None)
    per_page_param = request.args.get("per_page", None)
    sort = request.args.get("sort", None)

    if page_param is not None or per_page_param is not None:
        # User requested pagination
        try:
            page = int(page_param) if page_param is not None else 1
            per_page = int(per_page_param) if per_page_param is not None else 20
            page = max(page, 1)
            per_page = min(max(per_page, 1), 100)
            paginate = True
        except Exception:
            page, per_page = 1, 20
            paginate = True
    else:
        # No pagination requested
        page, per_page = None, None
        paginate = False
    try:
        executions, total = ExecutionService.get_executions(
            user=current_user,
            target_user_id=user_id,
            updated_at=updated_at,
            status=status,
            page=page,
            per_page=per_page,
            paginate=paginate,
            filter_param=filter_param,
            sort=sort,
        )
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    response_data = {
        "data": [
            execution.serialize(include, exclude, current_user)
            for execution in executions
        ]
    }
    # Only include pagination metadata if pagination was requested
    if paginate:
        response_data["page"] = page
        response_data["per_page"] = per_page
        response_data["total"] = total

    return jsonify(response_data), 200


@endpoints.route("/execution/<execution>", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_execution(execution):
    """
    Retrieve a specific execution by ID.

    **Authentication**: JWT token required
    **Access**: Users can only view their own executions (or admin can view any)

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include
      - Available: `script`, `script_name`, `user_name`, `user_email`, `logs`
    - `exclude`: Comma-separated list of fields to exclude
      - Available: `params`, `results`

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "abc123-def456",
        "script_id": "vegetation-analysis",
        "status": "SUCCESS",
        "params": {
          "region": "africa",
          "year_start": 2020,
          "year_end": 2023
        },
        "results": {
          "output_file": "vegetation_change.tif",
          "summary": "Analysis completed successfully"
        },
        "user_id": "user-789",
        "start_date": "2025-01-15T10:30:00Z",
        "end_date": "2025-01-15T11:45:00Z",
        "progress": 100
      }
    }
    ```

    **Field Control Examples**:
    - `?include=script` - Include full script details
    - `?include=script_name,user_name` - Include script and user names only
    - `?exclude=params,results` - Exclude large data fields

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Cannot access execution (not yours and not admin)
    - `404 Not Found`: Execution does not exist
    - `500 Internal Server Error`: Server error
    """
    logger.info("[ROUTER]: Getting execution: " + execution)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    try:
        execution = ExecutionService.get_execution(execution, current_user)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=execution.serialize(include, exclude, current_user)), 200


@endpoints.route("/execution/<execution>", strict_slashes=False, methods=["PATCH"])
@jwt_required()
@validate_execution_update
def update_execution(execution):
    """
    Update an execution's properties (admin only).

    **Authentication**: JWT token required
    **Authorization**: Admin level access required
    **Access**: Only admin users can update execution properties

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Request Body**:
    ```json
    {
      "status": "CANCELLED",
      "progress": 100,
      "results": {
        "reason": "User requested cancellation"
      }
    }
    ```

    **Updatable Fields**:
    - `status`: Execution status (PENDING, RUNNING, SUCCESS, FAILED, CANCELLED)
    - `progress`: Progress percentage (0-100)
    - `results`: Results data (JSON object)
    - `end_date`: End timestamp (ISO 8601 format)

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "abc123-def456",
        "script_id": "vegetation-analysis",
        "status": "CANCELLED",
        "progress": 100,
        "user_id": "user-789",
        "start_date": "2025-01-15T10:30:00Z",
        "end_date": "2025-01-15T10:35:00Z",
        "results": {
          "reason": "User requested cancellation"
        }
      }
    }
    ```

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: Execution does not exist
    - `422 Unprocessable Entity`: Invalid request data
    - `500 Internal Server Error`: Server error
    """
    logger.info("[ROUTER]: Updating execution " + execution)
    body = request.get_json()
    user = current_user
    if not can_access_admin_features(user):
        return error(status=403, detail="Forbidden")
    try:
        execution = ExecutionService.update_execution(body, execution)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=execution.serialize(user=current_user)), 200


@endpoints.route(
    "/execution/<execution>/cancel", strict_slashes=False, methods=["POST"]
)
@jwt_required()
def cancel_execution(execution):
    """
    Cancel a running execution and any associated Google Earth Engine tasks.

    **Authentication**: JWT token required
    **Access**: Users can cancel their own executions, ADMIN+ users can cancel any
    **Purpose**: Immediately stops execution processing, cleans up resources, and
    cancels any associated Google Earth Engine tasks that may have been started.

    **Path Parameters**:
    - `execution`: The ID of the execution to cancel

    **Request**: No request body required - this is a POST endpoint that triggers
    cancellation

    **Usage Examples**:
    ```bash
    # Cancel your own execution
    curl -X POST "https://api.trends.earth/api/v1/execution/abc123-def456/cancel" \
         -H "Authorization: Bearer your_jwt_token"

    # Admin canceling any user's execution
    curl -X POST "https://api.trends.earth/api/v1/execution/xyz789-uvw012/cancel" \
         -H "Authorization: Bearer admin_jwt_token"
    ```

    **Success Response Schema**:
    ```json
    {
      "data": {
        "execution": {
          "id": "abc123-def456",
          "script_id": "vegetation-analysis",
          "status": "CANCELLED",
          "params": {
            "region": "africa",
            "year_start": 2020,
            "year_end": 2023
          },
          "user_id": "user-789",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T10:35:00Z",
          "start_time": "2025-01-15T10:31:00Z",
          "end_time": "2025-01-15T10:35:00Z",
          "progress": 100
        },
        "cancellation_details": {
          "execution_id": "abc123-def456",
          "previous_status": "RUNNING",
          "docker_service_stopped": true,
          "docker_container_stopped": false,
          "gee_tasks_cancelled": [
            {
              "task_id": "6CIGR7EG2J45GJ2DN2J7X3WZ",
              "success": true,
              "error": null,
              "status": "CANCELLED"
            },
            {
              "task_id": "YBKKBHM2V63JYBVIPCCRY7A2",
              "success": true,
              "error": null,
              "status": "CANCELLED"
            }
          ],
          "errors": []
        }
      }
    }
    ```

    **Response Fields**:
    - `execution`: The updated execution object with CANCELLED status
    - `cancellation_details`: Detailed information about what was cancelled:
      - `execution_id`: ID of the cancelled execution
      - `previous_status`: Status before cancellation (e.g., "RUNNING", "PENDING")
      - `docker_service_stopped`: Whether Docker service was found and stopped
      - `docker_container_stopped`: Whether Docker container was found and stopped
      - `gee_tasks_cancelled`: Array of Google Earth Engine tasks that were cancelled
        - `task_id`: The GEE task identifier
        - `success`: Whether the cancellation was successful
        - `error`: Error message if cancellation failed
        - `status`: Final status of the GEE task
      - `errors`: Any errors encountered during cancellation process

    **Cancellation Process**:
    1. **Docker Resources**: Stops and removes Docker services/containers associated
       with the execution
    2. **GEE Task Detection**: Scans execution logs for Google Earth Engine task IDs
       using patterns like:
       - "Starting GEE task 6CIGR7EG2J45GJ2DN2J7X3WZ"
       - "Backing off ... for task YBKKBHM2V63JYBVIPCCRY7A2"
    3. **GEE Task Cancellation**: Uses Google Earth Engine REST API to cancel
       detected tasks
    4. **Status Update**: Sets execution status to CANCELLED and logs the cancellation

    **Cancellable States**:
    - `PENDING`: Execution queued, waiting to start
    - `READY`: Execution initialized and starting
    - `RUNNING`: Currently executing

    **Non-Cancellable States**:
    - `FINISHED`: Execution completed successfully
    - `FAILED`: Execution already failed
    - `CANCELLED`: Execution already cancelled

    **Error Responses**:
    - `400 Bad Request`: Execution is not in a cancellable state
      ```json
      {
        "status": 400,
        "detail": "Cannot cancel execution in FINISHED state"
      }
      ```
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: User can only cancel their own executions (unless admin)
      ```json
      {
        "status": 403,
        "detail": "You can only cancel your own executions"
      }
      ```
    - `404 Not Found`: Execution does not exist
    - `500 Internal Server Error`: Cancellation process failed

    **Partial Cancellation**: The endpoint will attempt to cancel all associated
    resources even if some steps fail. Check the `cancellation_details.errors` array
    for any issues encountered during the process.
    """
    logger.info(f"[ROUTER]: Canceling execution {execution}")
    user = current_user

    try:
        # Check if user can cancel this execution
        execution_obj = ExecutionService.get_execution(execution, user)
        if not execution_obj:
            return error(status=404, detail="Execution not found")

        # Only allow users to cancel their own executions, or admins to cancel any
        if not is_admin_or_higher(user) and execution_obj.user_id != user.id:
            return error(status=403, detail="You can only cancel your own executions")

        # Check if execution is in a cancellable state
        if execution_obj.status in ["FINISHED", "FAILED", "CANCELLED"]:
            return error(
                status=400,
                detail=f"Cannot cancel execution in {execution_obj.status} state",
            )

        # Cancel the execution
        result = ExecutionService.cancel_execution(execution)

        return jsonify(data=result), 200

    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Failed to cancel execution")


@endpoints.route("/execution/<execution>/log", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_execution_logs(execution):
    """
    Retrieve logs for a specific execution.

    **Authentication**: JWT token required
    **Access**: Execution owners, admins, or superadmins can view logs

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Query Parameters**:
    - `start`: Start timestamp for log filtering (ISO 8601 format)
    - `last-id`: Last log ID for pagination/incremental updates

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": "log-123",
          "execution_id": "abc123-def456",
          "timestamp": "2025-01-15T10:30:15Z",
          "level": "INFO",
          "message": "Processing started for region: africa",
          "details": {
            "step": "initialization",
            "progress": 5
          }
        },
        {
          "id": "log-124",
          "execution_id": "abc123-def456",
          "timestamp": "2025-01-15T10:30:45Z",
          "level": "INFO",
          "message": "Analysis 25% complete",
          "details": {
            "step": "processing",
            "progress": 25
          }
        }
      ]
    }
    ```

    **Usage Examples**:
    - `?start=2025-01-15T10:30:00Z` - Get logs after specific timestamp
    - `?last-id=log-120` - Get logs after specific log ID (pagination)
    - `?start=2025-01-15T10:30:00Z&last-id=log-120` - Combined filtering

    **Log Levels**:
    - `DEBUG`: Detailed diagnostic information
    - `INFO`: General information about execution progress
    - `WARNING`: Warning messages about potential issues
    - `ERROR`: Error messages about failures

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Cannot access execution (not yours and not admin)
    - `404 Not Found`: Execution does not exist
    - `500 Internal Server Error`: Server error
    """
    logger.info(f"[ROUTER]: Getting execution logs of execution {execution} ")
    try:
        # First verify user has access to this execution
        ExecutionService.get_execution(execution, current_user)

        # If access check passed, get the logs
        start = request.args.get("start", None)
        if start:
            start = dateutil.parser.parse(start)
        last_id = request.args.get("last-id", None)
        logs = ExecutionService.get_execution_logs(execution, start, last_id)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=[log.serialize() for log in logs]), 200


@endpoints.route(
    "/execution/<execution>/download-results", strict_slashes=False, methods=["GET"]
)
@jwt_required()
def get_download_results(execution):
    """Download execution results as a JSON file.

    **Authentication**: JWT token required
    **Access**: Execution owners, admins, or superadmins
    **Format**: Returns results as downloadable JSON file

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Response**: JSON file download
    - **Content-Type**: `text/plain`
    - **Content-Disposition**: `attachment; filename=results.json`
    - **Body**: JSON-formatted execution results

    **Use Cases**:
    - Download analysis results for offline processing
    - Archive execution outputs for record keeping
    - Share results with external stakeholders
    - Integrate with external reporting systems

    **Error Responses**:
    - `404 Not Found`: Execution does not exist or user lacks access
    - `500 Internal Server Error`: Failed to retrieve results
    """

    logger.info(f"[ROUTER]: Download execution results of execution {execution} ")
    try:
        execution_obj = ExecutionService.get_execution(execution, current_user)
    except ExecutionNotFound as exc:
        logger.error("[ROUTER]: " + exc.message)
        return error(status=404, detail=exc.message)
    except Exception as exc:
        logger.error("[ROUTER]: " + str(exc))
        return error(status=500, detail="Generic Error")

    results_payload = execution_obj.results or {}
    return Response(
        json.dumps(results_payload),
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment;filename=results.json"},
    )


@endpoints.route(
    "/execution/<execution>/docker-logs", strict_slashes=False, methods=["GET"]
)
@jwt_required()
def get_execution_docker_logs(execution):
    """
    Retrieve Docker service logs for a specific execution.

    **Authentication**: JWT token required
    **Access**: Restricted to ADMIN and SUPERADMIN users only
    **Purpose**: Provides raw Docker service logs for debugging and monitoring
      individual script executions.

    **Path Parameters**:
    - `execution`: The ID of the execution to retrieve logs for.

    **Success Response Schema**:
    ```json
    {
      "data": [
        {
          "id": 0,
          "created_at": "2025-08-04T10:30:00.123456Z",
          "text": "Log message from the container"
        },
        {
          "id": 1,
          "created_at": "2025-08-04T10:30:01.789012Z",
          "text": "Another log message"
        }
      ]
    }
    ```

    **Response Fields**:
    - `id`: A sequential identifier for the log line (for ordering).
    - `created_at`: The timestamp of the log entry (ISO 8601 format).
    - `text`: The content of the log line.

    **Error Responses**:
    - `401 Unauthorized`: JWT token required.
    - `403 Forbidden`: Insufficient privileges (ADMIN+ required).
    - `404 Not Found`: The specified execution or its logs do not exist.
    - `500 Internal Server Error`: Failed to retrieve logs due to a server-side
      issue.
    """
    logger.info(f"[ROUTER]: Getting docker logs for execution {execution}")
    user = current_user
    if not is_admin_or_higher(user):
        return error(status=403, detail="Forbidden")
    try:
        from gefapi.services.docker_service import DockerService

        logs = DockerService.get_service_logs(execution)
        if logs is None:
            return error(status=404, detail="Logs not found for execution")
        return jsonify(data=logs), 200
    except Exception as e:
        logger.error(f"[ROUTER]: Error getting docker logs: {e}")
        return error(status=500, detail="Internal Server Error")


@endpoints.route("/execution/<execution>/log", strict_slashes=False, methods=["POST"])
@jwt_required()
@validate_execution_log_creation
def create_execution_log(execution):
    """
    Create a log entry for a specific execution (admin only).

    **Authentication**: JWT token required
    **Authorization**: Admin level access required (ADMIN or SUPERADMIN)
    **Purpose**: Manually add log entries for debugging, monitoring, or audit purposes

    **Path Parameters**:
    - `execution`: Execution ID (UUID format)

    **Request Schema**:
    ```json
    {
      "level": "INFO",
      "message": "Custom log message for execution monitoring",
      "details": {
        "component": "manual_logging",
        "admin_action": true,
        "timestamp": "2025-01-15T10:30:00Z"
      }
    }
    ```

    **Request Fields**:
    - `level`: Log level - "DEBUG", "INFO", "WARNING", "ERROR" (required)
    - `message`: Log message content (required, max 1000 characters)
    - `details`: Additional structured data (optional JSON object)
    - `timestamp`: Log timestamp (optional, defaults to current time)

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "log-789",
        "execution_id": "abc123-def456",
        "timestamp": "2025-01-15T10:30:00Z",
        "level": "INFO",
        "message": "Custom log message for execution monitoring",
        "details": {
          "component": "manual_logging",
          "admin_action": true,
          "created_by": "admin-user-123"
        }
      }
    }
    ```

    **Use Cases**:
    - Add administrative notes to execution logs
    - Record manual interventions or troubleshooting steps
    - Supplement automated logs with human observations
    - Document resolution of execution issues

    **Log Integration**:
    - Manual logs appear alongside automated execution logs
    - Preserved in execution log history
    - Included in log exports and monitoring dashboards
    - Tagged with creating admin user information

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: Execution does not exist
    - `422 Unprocessable Entity`: Invalid request data or validation failed
    - `500 Internal Server Error`: Log creation failed
    """
    logger.info("[ROUTER]: Creating execution log for " + execution)
    body = request.get_json()
    user = current_user
    if not can_access_admin_features(user):
        return error(status=403, detail="Forbidden")
    try:
        log = ExecutionService.create_execution_log(body, execution)
    except ExecutionNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=log.serialize()), 200
