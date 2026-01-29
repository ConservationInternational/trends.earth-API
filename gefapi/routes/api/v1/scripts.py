"""Script management routes for the Trends.Earth API.

This module contains all endpoints related to script creation, management,
publishing, and retrieval operations.
"""

import logging
import os
import tempfile

import dateutil.parser
from flask import jsonify, request, send_from_directory
from flask_jwt_extended import current_user, jwt_required

from gefapi.errors import InvalidFile, NotAllowed, ScriptDuplicated, ScriptNotFound
from gefapi.routes.api.v1 import endpoints, error
from gefapi.s3 import get_script_from_s3
from gefapi.services import ScriptService
from gefapi.utils.permissions import can_access_admin_features, is_admin_or_higher
from gefapi.validators import validate_file

logger = logging.getLogger()


# SCRIPT CREATION
@endpoints.route("/script", strict_slashes=False, methods=["POST"])
@jwt_required()
@validate_file
def create_script():
    """
    Create a new script by uploading a script file.

    **Authentication**: JWT token required
    **Authorization**: Admin or Superadmin access required
    **Content-Type**: multipart/form-data (file upload)

    **Form Data**:
    - `file`: Script file to upload
      - Supported formats: Python scripts (.py), ZIP archives
      - Must contain valid script configuration
      - File size limits apply (configured server-side)

    **Script Requirements**:
    - Must include proper script metadata/configuration
    - Python scripts should follow trends.earth script structure
    - ZIP archives should contain main script file and dependencies

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "script-123",
        "slug": "my-new-script",
        "name": "New Analysis Script",
        "description": "Performs geospatial analysis",
        "status": "UPLOADED",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z",
        "user_id": "admin-456",
        "cpu": 1,
        "memory": 2048,
        "logs": false
      }
    }
    ```

    **Script Status After Creation**:
    - `UPLOADED`: Script successfully uploaded and validated
    - Further publishing required before execution is possible

    **Error Responses**:
    - `400 Bad Request`: Invalid file format, duplicate script, or validation failed
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required
    - `413 Payload Too Large`: File size exceeds limits
    - `500 Internal Server Error`: Script creation failed
    """
    logger.info("[ROUTER]: Creating a script")

    # Check if user is admin or superadmin
    if not is_admin_or_higher(current_user):
        return error(
            status=403, detail="Only admins and superadmins can create scripts"
        )

    sent_file = request.files.get("file")
    if sent_file.filename == "":
        sent_file.filename = "script"
    user = current_user
    try:
        script = ScriptService.create_script(sent_file, user)
    except InvalidFile as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except ScriptDuplicated as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=400, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=script.serialize()), 200


@endpoints.route("/script", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_scripts():
    """
    Retrieve all scripts with flexible filtering, sorting, and pagination.

    **Authentication**: JWT token required
    **Access**: Returns scripts visible to the current user based on permissions

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include in response
    - `exclude`: Comma-separated list of fields to exclude from response
    - `filter`: Search/filter scripts by name, description, or other attributes
    - `sort`: Sort field (prefix with '-' for descending, e.g., '-created_at')
    - `page`: Page number for pagination (triggers pagination when provided)
    - `per_page`: Items per page (1-100, default: 20, max without pagination: 2000)

    **Response Schema (without pagination)**:
    ```json
    {
      "data": [
        {
          "id": "script-123",
          "slug": "my-analysis-script",
          "name": "Land Use Analysis",
          "description": "Analyzes land use changes over time",
          "status": "PUBLISHED",
          "created_at": "2025-01-15T10:30:00Z",
          "updated_at": "2025-01-15T10:30:00Z",
          "user_id": "user-456",
          "cpu": 2,
          "memory": 4096,
          "logs": false
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
      "total": 150
    }
    ```

    **Script Status Values**:
    - `UPLOADED`: Script uploaded but not yet published
    - `PUBLISHED`: Script is available for execution
    - `UNPUBLISHED`: Script was published but later unpublished
    - `FAILED`: Script validation or processing failed

    **Filtering Examples**:
    - `?filter=land` - Find scripts with "land" in name or description
    - `?filter=status:PUBLISHED` - Find only published scripts
    - `?filter=user:john@example.com` - Find scripts by specific user

    **Sorting Examples**:
    - `?sort=name` - Sort by name ascending
    - `?sort=-created_at` - Sort by creation date descending
    - `?sort=status` - Sort by status

    **Field Control Examples**:
    - `?include=logs` - Include execution logs
    - `?include=user` - Include user information
    - `?exclude=description,logs` - Exclude verbose fields

    **Pagination Control**:
    - Without pagination: Returns up to 2000 scripts in single response
    - With pagination: `?page=1&per_page=20` - Returns paginated results

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `500 Internal Server Error`: Failed to retrieve scripts
    """
    logger.info("[ROUTER]: Getting all scripts")

    # Parse query parameters
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    filter_param = request.args.get("filter", None)
    sort = request.args.get("sort", None)

    # Pagination parameters - only paginate if user requests it
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
        page, per_page = 1, 2000
        paginate = False

    try:
        scripts, total = ScriptService.get_scripts(
            current_user,
            filter_param=filter_param,
            sort=sort,
            page=page,
            per_page=per_page,
            paginate=paginate,
        )

        response_data = {
            "data": [
                script.serialize(include, exclude, current_user) for script in scripts
            ]
        }
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")

    if paginate:
        response_data["page"] = page
        response_data["per_page"] = per_page
        response_data["total"] = total

    return jsonify(response_data), 200


@endpoints.route("/script/<script>", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_script(script):
    """
    Retrieve details for a specific script by ID or slug.

    **Authentication**: JWT token required
    **Access**: Returns script details visible to the current user based on permissions

    **Path Parameters**:
    - `script`: Script identifier/slug or numeric ID

    **Query Parameters**:
    - `include`: Comma-separated list of additional fields to include in response
      - Available: `logs`, `user`, `executions`, `access_controls`
    - `exclude`: Comma-separated list of fields to exclude from response
      - Available: `description`, `logs`, `user_id`

    **Response Schema**:
    ```json
    {
      "data": {
        "id": "script-123",
        "slug": "vegetation-analysis",
        "name": "Vegetation Change Analysis",
        "description": "Analyzes vegetation changes using satellite imagery",
        "status": "PUBLISHED",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:30:00Z",
        "user_id": "user-456",
        "cpu": 2,
        "memory": 4096,
        "logs": false
      }
    }
    ```

    **Script Status Values**:
    - `UPLOADED`: Script uploaded but not yet published
    - `PUBLISHED`: Script is available for execution
    - `UNPUBLISHED`: Script was published but later unpublished
    - `FAILED`: Script validation or processing failed

    **Field Control Examples**:
    - `?include=user` - Include user information who created the script
    - `?include=logs` - Include script processing logs
    - `?exclude=description` - Exclude verbose description field

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `404 Not Found`: Script does not exist or user doesn't have access
    - `500 Internal Server Error`: Server error
    """
    logger.info("[ROUTER]: Getting script " + script)
    include = request.args.get("include")
    include = include.split(",") if include else []
    exclude = request.args.get("exclude")
    exclude = exclude.split(",") if exclude else []
    try:
        script = ScriptService.get_script(script, current_user)
        serialized = script.serialize(include, exclude, current_user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=serialized), 200


@endpoints.route("/script/<script>/publish", strict_slashes=False, methods=["POST"])
@jwt_required()
def publish_script(script):
    """
    Publish a script to make it available for execution.

    **Authentication**: JWT token required
    **Authorization**: Admin or script owner access required
    **Purpose**: Makes an uploaded script available for execution by authorized users

    **Path Parameters**:
    - `script`: Script identifier/slug or numeric ID

    **Request**: No request body required

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "script-123",
        "slug": "vegetation-analysis",
        "name": "Vegetation Change Analysis",
        "description": "Analyzes vegetation changes using satellite imagery",
        "status": "PUBLISHED",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:35:00Z",
        "user_id": "user-456",
        "cpu": 2,
        "memory": 4096,
        "logs": false
      }
    }
    ```

    **Publishing Process**:
    - Validates script configuration and dependencies
    - Updates script status from UPLOADED to PUBLISHED
    - Makes script available in public script listings
    - Enables script execution for authorized users

    **Prerequisites**:
    - Script must be in UPLOADED status
    - Script must have valid configuration
    - User must have publishing permissions (admin or script owner)

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient permissions to publish script
    - `404 Not Found`: Script does not exist
    - `400 Bad Request`: Script not in valid state for publishing
    - `500 Internal Server Error`: Publishing failed
    """
    logger.info("[ROUTER]: Publishing script " + script)
    try:
        script = ScriptService.publish_script(script, current_user)
        serialized = script.serialize(user=current_user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=serialized), 200


@endpoints.route("/script/<script>/unpublish", strict_slashes=False, methods=["POST"])
@jwt_required()
def unpublish_script(script):
    """
    Unpublish a script to make it unavailable for new executions.

    **Authentication**: JWT token required
    **Authorization**: Admin or script owner access required
    **Purpose**: Removes a published script from public availability while
      preserving data

    **Path Parameters**:
    - `script`: Script identifier/slug or numeric ID

    **Request**: No request body required

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "script-123",
        "slug": "vegetation-analysis",
        "name": "Vegetation Change Analysis",
        "description": "Analyzes vegetation changes using satellite imagery",
        "status": "UNPUBLISHED",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T10:35:00Z",
        "user_id": "user-456",
        "cpu": 2,
        "memory": 4096,
        "logs": false
      }
    }
    ```

    **Unpublishing Effects**:
    - Updates script status from PUBLISHED to UNPUBLISHED
    - Removes script from public script listings
    - Prevents new executions from being started
    - Existing running executions continue to completion
    - Script data and configuration are preserved

    **Use Cases**:
    - Temporarily disable a script for maintenance
    - Remove deprecated or problematic scripts
    - Control script availability during updates

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient permissions to unpublish script
    - `404 Not Found`: Script does not exist
    - `400 Bad Request`: Script not in valid state for unpublishing
    - `500 Internal Server Error`: Unpublishing failed
    """
    logger.info("[ROUTER]: Unpublishsing script " + script)
    try:
        script = ScriptService.unpublish_script(script, current_user)
        serialized = script.serialize(user=current_user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=serialized), 200


@endpoints.route("/script/<script>/download", strict_slashes=False, methods=["GET"])
@jwt_required()
def download_script(script):
    """
    Download a script's source code and files as a compressed archive.

    **Authentication**: JWT token required
    **Access**: Script must be accessible to current user based on permissions
    **Format**: Returns compressed tar.gz archive containing script files

    **Path Parameters**:
    - `script`: Script identifier/slug or numeric ID

    **Response**: Binary file download (tar.gz archive)
    - **Content-Type**: `application/gzip`
    - **Content-Disposition**: `attachment; filename=script-name.tar.gz`

    **Archive Contents**:
    - Main script file(s) (Python, R, or other supported languages)
    - Configuration files (script metadata, requirements)
    - Dependencies and libraries (if included)
    - Documentation and README files
    - Supporting data files (if any)

    **Usage Examples**:
    ```bash
    # Download script archive
    curl -H "Authorization: Bearer your_jwt_token" \
         -o vegetation-analysis.tar.gz \
         "https://api.trends.earth/api/v1/script/vegetation-analysis/download"

    # Extract downloaded archive
    tar -xzf vegetation-analysis.tar.gz
    ```

    **Access Control**:
    - Users can download scripts they have execution access to
    - Admin users can download any script
    - Private scripts require explicit permission

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: No access to download this script
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Download failed or file unavailable
    """
    logger.info("[ROUTER]: Download script " + script)
    try:
        script = ScriptService.get_script(script, current_user)

        temp_dir = tempfile.TemporaryDirectory().name
        script_file = script.slug + ".tar.gz"
        out_path = os.path.join(temp_dir, script_file)
        get_script_from_s3(script_file, out_path)

        return send_from_directory(directory=temp_dir, filename=script_file)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except NotAllowed as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=403, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")


@endpoints.route("/script/<script>/log", strict_slashes=False, methods=["GET"])
@jwt_required()
def get_script_logs(script):
    """
    Retrieve processing and validation logs for a specific script.

    **Authentication**: JWT token required
    **Access**: Script must be accessible to current user
    **Purpose**: View script upload, validation, and publishing logs for debugging

    **Path Parameters**:
    - `script`: Script identifier/slug or numeric ID

    **Query Parameters**:
    - `start`: Start timestamp for log filtering (ISO 8601 format)
      - Example: `?start=2025-01-15T10:30:00Z`
    - `last-id`: Last log ID for pagination/incremental updates
      - Example: `?last-id=log-456`

    **Response Schema**:
    ```json
    {
      "data": [
        {
          "id": "log-123",
          "script_id": "script-456",
          "timestamp": "2025-01-15T10:30:00Z",
          "level": "INFO",
          "message": "Script validation started",
          "details": {
            "stage": "validation",
            "file_count": 5,
            "total_size": "2.3MB"
          }
        },
        {
          "id": "log-124",
          "script_id": "script-456",
          "timestamp": "2025-01-15T10:30:15Z",
          "level": "SUCCESS",
          "message": "Script validation completed successfully",
          "details": {
            "stage": "validation",
            "duration": "15s"
          }
        }
      ]
    }
    ```

    **Log Levels**:
    - `DEBUG`: Detailed diagnostic information
    - `INFO`: General information about script processing
    - `WARNING`: Warning messages about potential issues
    - `ERROR`: Error messages about failures
    - `SUCCESS`: Successful completion of operations

    **Log Types**:
    - Upload processing logs
    - Script validation and syntax checking
    - Dependency resolution logs
    - Publishing process logs
    - Configuration validation results

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: No access to view script logs
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Failed to retrieve logs
    """
    logger.info(f"[ROUTER]: Getting script logs of script {script} ")
    try:
        start = request.args.get("start", None)
        if start:
            start = dateutil.parser.parse(start)
        last_id = request.args.get("last-id", None)
        logs = ScriptService.get_script_logs(script, start, last_id)
        serialized = [log.serialize() for log in logs]
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=serialized), 200


@endpoints.route("/script/<script>", strict_slashes=False, methods=["PATCH"])
@jwt_required()
@validate_file
def update_script(script):
    """
    Update an existing script by uploading a new version.

    **Authentication**: JWT token required
    **Authorization**: Admin or script owner access required
    **Content-Type**: multipart/form-data (file upload)

    **Path Parameters**:
    - `script`: Script identifier/slug or numeric ID to update

    **Form Data**:
    - `file`: New script file to upload
      - Supported formats: Python scripts (.py), ZIP archives
      - Must contain valid script configuration
      - File size limits apply (configured server-side)

    **Update Process**:
    - Validates new script file format and content
    - Preserves script metadata (name, description, permissions)
    - Updates script files and dependencies
    - Resets status to UPLOADED (requires re-publishing if was published)
    - Maintains execution history and logs

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "script-123",
        "slug": "vegetation-analysis",
        "name": "Vegetation Change Analysis",
        "description": "Analyzes vegetation changes using satellite imagery",
        "status": "UPLOADED",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T11:15:00Z",
        "user_id": "user-456",
        "cpu": 2,
        "memory": 4096,
        "logs": false
      }
    }
    ```

    **Status Changes**:
    - Published scripts become UPLOADED and require re-publishing
    - Failed scripts can be updated to fix issues
    - Update preserves access controls and user permissions

    **Error Responses**:
    - `400 Bad Request`: Invalid file format or validation failed
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Insufficient permissions to update script
    - `404 Not Found`: Script does not exist
    - `413 Payload Too Large`: File size exceeds limits
    - `500 Internal Server Error`: Script update failed
    """
    logger.info("[ROUTER]: Updating a script")
    sent_file = request.files.get("file")
    if sent_file.filename == "":
        sent_file.filename = "script"
    user = current_user
    try:
        updated_script = ScriptService.update_script(script, sent_file, user)
        return jsonify(data=updated_script.serialize(user=current_user)), 200
    except (InvalidFile, ScriptNotFound, NotAllowed) as e:
        status_code = 400
        if isinstance(e, ScriptNotFound):
            status_code = 404
        elif isinstance(e, NotAllowed):
            status_code = 403
        logger.error(f"[ROUTER]: {e.message}")
        return error(status=status_code, detail=e.message)
    except Exception as e:
        logger.error(f"[ROUTER]: {e}")
        return error(status=500, detail=str(e))


@endpoints.route("/script/<script>", strict_slashes=False, methods=["DELETE"])
@jwt_required()
def delete_script(script):
    """
    Permanently delete a script and all associated data.

    **Authentication**: JWT token required
    **Authorization**: Admin access required (ADMIN or SUPERADMIN)
    **Warning**: This action is irreversible and deletes all associated data

    **Path Parameters**:
    - `script`: Script identifier/slug or numeric ID to delete

    **Deletion Process**:
    - Cancels any running executions using this script
    - Deletes all execution history and logs for this script
    - Removes script files and dependencies from storage
    - Deletes script metadata and configuration
    - Removes access control settings
    - Cleans up any associated resources

    **Success Response Schema**:
    ```json
    {
      "data": {
        "id": "script-123",
        "slug": "vegetation-analysis",
        "name": "Vegetation Change Analysis",
        "description": "Analyzes vegetation changes using satellite imagery",
        "status": "DELETED",
        "created_at": "2025-01-15T10:30:00Z",
        "updated_at": "2025-01-15T12:00:00Z",
        "user_id": "user-456",
        "cpu": 2,
        "memory": 4096,
        "logs": false
      }
    }
    ```

    **Impact**:
    - All executions using this script will be cancelled
    - Historical execution data is permanently lost
    - Script cannot be recovered after deletion
    - Users lose access to download script files

    **Security Considerations**:
    - Only admin users can delete scripts
    - Action is logged for audit purposes
    - Confirmation should be required in client applications

    **Error Responses**:
    - `401 Unauthorized`: JWT token required
    - `403 Forbidden`: Admin access required
    - `404 Not Found`: Script does not exist
    - `500 Internal Server Error`: Deletion failed
    """
    logger.info("[ROUTER]: Deleting script: " + script)
    identity = current_user
    if not can_access_admin_features(identity):
        return error(status=403, detail="Forbidden")
    try:
        script = ScriptService.delete_script(script, identity)
        serialized = script.serialize(user=current_user)
    except ScriptNotFound as e:
        logger.error("[ROUTER]: " + e.message)
        return error(status=404, detail=e.message)
    except Exception as e:
        logger.error("[ROUTER]: " + str(e))
        return error(status=500, detail="Generic Error")
    return jsonify(data=serialized), 200
