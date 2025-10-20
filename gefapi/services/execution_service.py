"""SCRIPT SERVICE"""

import datetime
import logging
import os
from uuid import UUID

import rollbar
from sqlalchemy import case, func

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import ExecutionNotFound, ScriptNotFound, ScriptStateNotValid
from gefapi.models import Execution, ExecutionLog, Script, StatusLog, User
from gefapi.services import EmailService, ScriptService, UserService, docker_run
from gefapi.utils.permissions import is_admin_or_higher

# Import celery at module level for testing
try:
    from gefapi import celery as celery_app
except ImportError:
    celery_app = None

logger = logging.getLogger()

EXECUTION_FINISHED_MAIL_CONTENT = """
<p>Thank you for using Trends.Earth. The below task has {}. More details on this task
are below: </p>
<ul>
    <li>Task name: {}</li>
    <li>Job: {}</li>
    <li>Task ID: {}</li>
    <li>Start time: {}</li>
    <li>End time: {}</li>
    <li>Status: {}</li>
</ul>
<p>For more information, and to view the results, return to QGIS and click the
"Datasets" tab in the Trends.Earth plugin window.</p>
<p>Thank you, </br>The Trends.Earth team</p>
"""


def dict_to_query(params):
    """
    Convert dictionary parameters to URL query string format.

    Args:
        params (dict): Dictionary of key-value pairs

    Returns:
        str: URL-encoded query string without leading '?'
    """
    query = ""
    for key in params:
        query += key + "=" + params.get(key) + "&"
    return query[0:-1]


def update_execution_status_with_logging(
    execution, new_status, additional_objects=None, explicit_progress=None
):
    """
    Helper function to update execution status and log to status_log table.

    This function should be called whenever a script's status field changes.
    It updates the execution status and creates a single status_log entry
    AFTER the change with information about the status transition.

    Args:
        execution (Execution): The execution object to update
        new_status (str): The new status to set
        additional_objects (list, optional): Additional objects to add to the same
            transaction
        explicit_progress (int, optional): If provided, don't auto-set progress to 100
            for terminal states

    Returns:
        StatusLog: The created status log entry
    """
    logger.info(f"[SERVICE]: Updating execution {execution.id} status to {new_status}")

    # Store the old status before updating
    old_status = execution.status

    try:
        # Update the execution status first
        execution.status = new_status

        # Update end_date and progress for terminal states
        if new_status in ["FINISHED", "FAILED", "CANCELLED"]:
            execution.end_date = datetime.datetime.utcnow()
            # Only set progress to 100 if no explicit progress was provided
            if explicit_progress is None:
                execution.progress = 100

        # Count current executions by status AFTER making the change
        logger.info(
            "[SERVICE]: Counting executions by status for status log (after change)"
        )

        status_counts = (
            db.session.query(Execution.status, func.count(Execution.id).label("count"))
            .group_by(Execution.status)
            .all()
        )

        # Convert to dictionary for counts
        count_dict = dict(status_counts)

        # Map to the expected field names
        executions_pending = count_dict.get("PENDING", 0)
        executions_ready = count_dict.get("READY", 0)
        executions_running = count_dict.get("RUNNING", 0)
        executions_finished = count_dict.get("FINISHED", 0)
        executions_failed = count_dict.get("FAILED", 0)
        executions_cancelled = count_dict.get("CANCELLED", 0)

        logger.info(
            f"[SERVICE]: Status counts AFTER change - "
            f"Pending: {executions_pending}, Ready: {executions_ready}, "
            f"Running: {executions_running}, "
            f"Finished: {executions_finished}, "
            f"Failed: {executions_failed}, "
            f"Cancelled: {executions_cancelled}"
        )

        # Create status log entry for the transition
        status_log = StatusLog(
            executions_pending=executions_pending,
            executions_ready=executions_ready,
            executions_running=executions_running,
            executions_finished=executions_finished,
            executions_failed=executions_failed,
            executions_cancelled=executions_cancelled,
            status_from=old_status,
            status_to=new_status,
            execution_id=str(execution.id),
        )

        # Save execution and status log
        db.session.add(execution)
        db.session.add(status_log)

        # Add any additional objects to the same transaction
        if additional_objects:
            for obj in additional_objects:
                db.session.add(obj)

        db.session.commit()

        logger.info(
            f"[SERVICE]: Status log created with ID {status_log.id} for execution "
            f"{execution.id} status change {old_status} -> {new_status}"
        )

        return status_log

    except Exception as error:
        logger.error(
            f"[SERVICE]: Error updating execution status with logging: {error}"
        )
        db.session.rollback()
        rollbar.report_exc_info()
        raise error


class ExecutionService:
    """
    Service class for managing execution lifecycle and operations.

    This service handles all execution-related operations including:
    - Creating new executions from script templates
    - Querying and filtering executions with permissions
    - Updating execution status with automatic status logging
    - Cancelling running executions and associated resources
    - Managing execution logs and monitoring

    All status updates use the centralized status tracking system to maintain
    an audit trail of execution state changes.
    """

    @staticmethod
    def _build_execution_environment(user, execution_id):
        """
        Build environment variables for script execution container.

        Args:
            user: User model instance executing the script
            execution_id: ID of the execution

        Returns:
            dict: Environment variables to pass to the container
        """
        # Start with base environment variables
        environment = SETTINGS.get("environment", {}).copy()
        environment["EXECUTION_ID"] = execution_id

        # Maintain backward compatibility for trends.earth-Environment by
        # exposing legacy variable names alongside the new defaults.
        environment_user = environment.get("API_ENVIRONMENT_USER")
        if environment_user and "API_USER" not in environment:
            environment["API_USER"] = environment_user

        environment_password = environment.get("API_ENVIRONMENT_USER_PASSWORD")
        if environment_password and "API_PASSWORD" not in environment:
            environment["API_PASSWORD"] = environment_password

        # Add GEE authentication based on user's credential type
        if user and user.has_gee_credentials():
            if user.gee_credentials_type == "oauth":
                # Add OAuth credentials for GEE authentication
                logger.info(
                    f"Adding OAuth credentials for user {user.email} "
                    f"execution {execution_id}"
                )

                access_token, refresh_token = user.get_gee_oauth_credentials()
                if access_token and refresh_token:
                    environment.update(
                        {
                            "GEE_OAUTH_ACCESS_TOKEN": access_token,
                            "GEE_OAUTH_REFRESH_TOKEN": refresh_token,
                            "GOOGLE_OAUTH_CLIENT_ID": environment.get(
                                "GOOGLE_OAUTH_CLIENT_ID"
                            ),
                            "GOOGLE_OAUTH_CLIENT_SECRET": environment.get(
                                "GOOGLE_OAUTH_CLIENT_SECRET"
                            ),
                            "GOOGLE_OAUTH_TOKEN_URI": environment.get(
                                "GOOGLE_OAUTH_TOKEN_URI"
                            ),
                        }
                    )
                    logger.info(
                        f"OAuth environment variables added for user {user.email}"
                    )
                else:
                    logger.warning(
                        f"User {user.email} has OAuth credential type "
                        "but missing tokens"
                    )

            elif user.gee_credentials_type == "service_account":
                # Add user's service account credentials
                logger.info(
                    f"Adding user service account credentials for user "
                    f"{user.email} execution {execution_id}"
                )

                service_account_data = user.get_gee_service_account()
                if service_account_data:
                    import base64
                    import json

                    # Encode service account as base64 for container
                    service_account_json = json.dumps(service_account_data)
                    service_account_b64 = base64.b64encode(
                        service_account_json.encode()
                    ).decode()
                    environment["EE_SERVICE_ACCOUNT_JSON"] = service_account_b64
                    logger.info(
                        f"User service account credentials added for user {user.email}"
                    )
                else:
                    logger.warning(
                        f"User {user.email} has service account credential type "
                        "but missing data"
                    )

        # If no user credentials, fall back to default service account (if configured)
        if not user or not user.has_gee_credentials():
            if environment.get("EE_SERVICE_ACCOUNT_JSON"):
                logger.info(
                    f"Using default service account for execution {execution_id}"
                )
            else:
                logger.warning(
                    f"No GEE credentials available for execution {execution_id}. "
                    f"EE_SERVICE_ACCOUNT_JSON in environment: "
                    f"{bool(environment.get('EE_SERVICE_ACCOUNT_JSON'))}, "
                    f"EE_SERVICE_ACCOUNT_JSON from OS env: "
                    f"{bool(os.getenv('EE_SERVICE_ACCOUNT_JSON'))}"
                )

        return environment

    """Execution Class"""

    @staticmethod
    def get_executions(
        user,
        target_user_id=None,
        updated_at=None,
        status=None,
        page=1,
        per_page=2000,
        paginate=True,
        filter_param=None,
        sort=None,
    ):
        """
        Retrieve executions with filtering, pagination, and permission controls.

        Args:
            user: User object for permission checking
            target_user_id (str, optional): Filter by specific user ID (admin only)
            updated_at (datetime, optional): Filter by start date
            status (str, optional): Filter by execution status
            page (int): Page number for pagination (default: 1)
            per_page (int): Results per page (default: 2000, max: 2000)
            paginate (bool): Whether to apply pagination (default: True)
            filter_param (str, optional): SQL-style filter expressions
            sort (str, optional): SQL-style sort expressions

        Returns:
            tuple: (executions list, total count)

        Raises:
            Exception: If pagination parameters are invalid or filter permissions denied
        """
        logger.info("[SERVICE]: Getting executions")
        logger.info("[DB]: QUERY")

        # Validate pagination parameters only when pagination is requested
        if paginate:
            if page < 1:
                raise Exception("Page must be greater than 0")
            if per_page < 1:
                raise Exception("Per page must be greater than 0")

        query = db.session.query(Execution)

        # Apply user filters
        if is_admin_or_higher(user):
            if target_user_id:
                try:
                    # If target_user_id is already a UUID object, use it directly
                    if isinstance(target_user_id, UUID):
                        validated_user_id = target_user_id
                    else:
                        validated_user_id = UUID(target_user_id, version=4)
                except Exception as error:
                    rollbar.report_exc_info()
                    raise error
                query = query.filter(Execution.user_id == validated_user_id)
        else:
            # For non-admin users, only show their own executions
            query = query.filter(Execution.user_id == user.id)

        # Apply other filters
        if status:
            query = query.filter(func.lower(Execution.status) == status.lower())
        if updated_at:
            # Filter by start_date since that's when executions begin
            # and is more reliable than end_date for ongoing executions
            query = query.filter(Execution.start_date >= updated_at)

        # Apply SQL-style filter_param if present
        if filter_param:
            import re

            from sqlalchemy import and_

            filter_clauses = []
            join_scripts = False
            join_users = False
            for expr in filter_param.split(","):
                expr = expr.strip()
                m = re.match(
                    r"(\w+)\s*(=|!=|>=|<=|>|<| like )\s*(.+)", expr, re.IGNORECASE
                )
                if m:
                    field, op, value = m.groups()
                    field = field.strip().lower()
                    op = op.strip().lower()
                    value = value.strip().strip("'\"")

                    if field == "script_name":
                        join_scripts = True
                        col = Script.name
                    elif field == "user_name":
                        if not is_admin_or_higher(user):
                            raise Exception(
                                "Only admin or superadmin users can filter by user_name"
                            )
                        join_users = True
                        col = User.name
                    elif field == "user_email":
                        if not is_admin_or_higher(user):
                            raise Exception(
                                "Only admin or superadmin users can "
                                "filter by user_email"
                            )
                        join_users = True
                        col = User.email
                    else:
                        col = getattr(Execution, field, None)
                    if col is not None:
                        # Check if this is a string column for case-insensitive compare
                        is_string_col = (
                            field in ["script_name", "user_name", "user_email"]
                            or (
                                hasattr(col.type, "python_type")
                                and isinstance(col.type.python_type, type)
                                and issubclass(col.type.python_type, str)
                            )
                            or str(col.type)
                            .upper()
                            .startswith(("VARCHAR", "TEXT", "STRING"))
                        )

                        if op == "=":
                            if is_string_col:
                                filter_clauses.append(func.lower(col) == value.lower())
                            else:
                                filter_clauses.append(col == value)
                        elif op == "!=":
                            if is_string_col:
                                filter_clauses.append(func.lower(col) != value.lower())
                            else:
                                filter_clauses.append(col != value)
                        elif op == ">":
                            filter_clauses.append(col > value)
                        elif op == "<":
                            filter_clauses.append(col < value)
                        elif op == ">=":
                            filter_clauses.append(col >= value)
                        elif op == "<=":
                            filter_clauses.append(col <= value)
                        elif op == "like":
                            filter_clauses.append(col.ilike(value))
            # Join with script and user tables if needed due to filtering on
            # fields not in executions table
            if join_scripts:
                query = query.join(Script, Execution.script_id == Script.id)
            if join_users:
                query = query.join(User, Execution.user_id == User.id)
            if filter_clauses:
                query = query.filter(and_(*filter_clauses))

        # Apply SQL-style sorting if present
        if sort:
            from sqlalchemy import asc, desc

            for sort_expr in sort.split(","):
                sort_expr = sort_expr.strip()
                if not sort_expr:
                    continue
                parts = sort_expr.split()
                field = parts[0].lower()
                direction = parts[1].lower() if len(parts) > 1 else "asc"
                col = getattr(Execution, field, None)
                if col is not None:
                    if direction == "desc":
                        query = query.order_by(desc(col))
                    else:
                        query = query.order_by(asc(col))
                elif field == "duration":
                    duration_expr = case(
                        (
                            Execution.end_date.isnot(None),
                            func.extract(
                                "epoch", Execution.end_date - Execution.start_date
                            ),
                        ),
                        else_=func.extract("epoch", func.now() - Execution.start_date),
                    )
                    if direction == "desc":
                        query = query.order_by(duration_expr.desc())
                    else:
                        query = query.order_by(duration_expr.asc())
                elif field == "script_name":
                    if direction == "desc":
                        query = query.join(
                            Script, Execution.script_id == Script.id
                        ).order_by(Script.name.desc())
                    else:
                        query = query.join(
                            Script, Execution.script_id == Script.id
                        ).order_by(Script.name.asc())
                elif field == "user_email":
                    if not is_admin_or_higher(user):
                        raise Exception(
                            "Only admin or superadmin users can sort by user_email"
                        )
                    if direction == "desc":
                        query = query.join(User, Execution.user_id == User.id).order_by(
                            User.email.desc()
                        )
                    else:
                        query = query.join(User, Execution.user_id == User.id).order_by(
                            User.email.asc()
                        )
                elif field == "user_name":
                    if not is_admin_or_higher(user):
                        raise Exception(
                            "Only admin or superadmin users can sort by user_name"
                        )
                    if direction == "desc":
                        query = query.join(User, Execution.user_id == User.id).order_by(
                            User.name.desc()
                        )
                    else:
                        query = query.join(User, Execution.user_id == User.id).order_by(
                            User.name.asc()
                        )
        else:
            # Default to sorting by end_date for backwards compatibility
            query = query.order_by(Execution.end_date.desc())

        if paginate:
            total = query.count()
            executions = query.offset((page - 1) * per_page).limit(per_page).all()
        else:
            # Apply a reasonable default limit when pagination is not requested
            # to prevent timeouts with users who have large numbers of executions
            default_limit = 1000
            logger.warning(
                f"[SERVICE]: No pagination requested, applying default "
                f"limit of {default_limit} executions"
            )
            executions = query.limit(default_limit).all()
            total = len(executions)
            # If we hit the limit, log a warning that there may be more results
            if len(executions) == default_limit:
                logger.warning(
                    f"[SERVICE]: Retrieved {default_limit} executions (limit reached). "
                    "Consider using pagination for complete results."
                )

        return executions, total

    @staticmethod
    def create_execution(script_id, params, user):
        """
        Create a new execution from a script template.

        Args:
            script_id (str): UUID of the script to execute
            params (dict): Execution parameters and configuration
            user: User object creating the execution

        Returns:
            Execution: Created execution object

        Raises:
            ScriptNotFound: If script doesn't exist
            ScriptStateNotValid: If script is not in SUCCESS state
        """
        logger.info("[SERVICE]: Creating execution")
        script = ScriptService.get_script(script_id, user)
        if not script:
            raise ScriptNotFound(
                message="Script with id " + script_id + " does not exist"
            )
        if script.status != "SUCCESS":
            raise ScriptStateNotValid(
                message="Script with id " + script_id + " is not BUILT"
            )
        execution = Execution(script_id=script.id, params=params, user_id=user.id)
        try:
            logger.info("[DB]: ADD")
            db.session.add(execution)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error

        try:
            environment = ExecutionService._build_execution_environment(
                user, execution.id
            )
            docker_run.delay(execution.id, script.slug, environment, params)
        except Exception as e:
            rollbar.report_exc_info()
            raise e
        return execution

    @staticmethod
    def get_execution(execution_id, user="fromservice"):
        """
        Retrieve a single execution by ID with permission checking.

        Args:
            execution_id (str|UUID): UUID of the execution to retrieve
            user (User|str): User object for permission checking, or "fromservice"
                           for internal service calls

        Returns:
            Execution: The requested execution object

        Raises:
            ExecutionNotFound: If execution doesn't exist or user lacks permission
        """
        logger.info(f"[SERVICE]: Getting execution {execution_id}")
        logger.info("[DB]: QUERY")
        # user = 'from service' just in case the requests comes from the service
        if user == "fromservice" or is_admin_or_higher(user):
            try:
                # If execution_id is already a UUID object, use it directly
                if isinstance(execution_id, UUID):
                    execution = Execution.query.filter_by(id=execution_id).first()
                else:
                    UUID(execution_id, version=4)
                    execution = Execution.query.filter_by(id=execution_id).first()
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        else:
            try:
                # If execution_id is already a UUID object, use it directly
                if isinstance(execution_id, UUID):
                    execution = (
                        db.session.query(Execution)
                        .filter(Execution.id == execution_id)
                        .filter(Execution.user_id == user.id)
                        .first()
                    )
                else:
                    UUID(execution_id, version=4)
                    execution = (
                        db.session.query(Execution)
                        .filter(Execution.id == execution_id)
                        .filter(Execution.user_id == user.id)
                        .first()
                    )
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        if not execution:
            raise ExecutionNotFound(message="Ticket Not Found")
        return execution

    @staticmethod
    def update_execution(execution, execution_id):
        """
        Update execution properties including status, progress, and results.

        Updates an execution's status, progress, or results. For terminal status
        updates (FINISHED, FAILED, CANCELLED), automatically sends email
        notifications to users who have email notifications enabled.

        When status is updated, this method uses the centralized status tracking
        system to automatically create status log entries and send notification
        emails for terminal states.

        Args:
            execution (dict): Dictionary containing fields to update:
                - status (str, optional): New execution status
                - progress (int, optional): Execution progress percentage
                - results (dict, optional): Execution results data
            execution_id (str): UUID of the execution to update

        Returns:
            Execution: Updated execution object

        Raises:
            ExecutionNotFound: If execution with given ID doesn't exist
            Exception: If no valid fields provided for update

        Notes:
            - Email notifications are sent only for terminal states when user
              has email_notifications_enabled=True
            - Status updates trigger status logging via helper function
            - Email failures don't prevent execution status updates
        """
        logger.info("[SERVICE]: Updating execution")
        status = execution.get("status", None)
        progress = execution.get("progress", None)
        results = execution.get("results", None)
        if status is None and progress is None and results is None:
            raise Exception
        execution = ExecutionService.get_execution(execution_id=execution_id)
        if not execution:
            raise ExecutionNotFound(
                message="Execution with id " + execution_id + " does not exist"
            )

        # Update progress and results first (regardless of status update)
        if progress is not None:
            execution.progress = progress
        if results is not None:
            execution.results = results

        # Use the new helper function for status updates
        if status is not None:
            # Update status with logging, pass explicit progress if provided
            update_execution_status_with_logging(
                execution, status, explicit_progress=progress
            )

            # Send notification email for terminal states
            if status in ["FINISHED", "FAILED", "CANCELLED"]:
                user = UserService.get_user(str(execution.user_id))
                script = ScriptService.get_script(str(execution.script_id))

                # Check if user has email notifications enabled
                if user.email_notifications_enabled:
                    try:
                        EmailService.send_html_email(
                            recipients=[user.email],
                            html=EXECUTION_FINISHED_MAIL_CONTENT.format(
                                status,
                                execution.params.get("task_name"),
                                script.name,
                                str(execution.id),
                                execution.start_date,
                                execution.end_date or datetime.datetime.utcnow(),
                                status,
                            ),
                            subject="[trends.earth] Execution finished",
                        )
                    except Exception:
                        rollbar.report_exc_info()
                        logger.info("Failed to send email - check email service")
                else:
                    logger.info(
                        f"Email notification skipped for user {user.email} - "
                        "notifications disabled"
                    )

        else:
            # For non-status updates, need to commit the progress/results changes
            try:
                logger.info("[DB]: ADD")
                db.session.add(execution)
                db.session.commit()
            except Exception as error:
                rollbar.report_exc_info()
                raise error

        return execution

    @staticmethod
    def create_execution_log(log, execution_id):
        """
        Create a new log entry for an execution.

        Args:
            log (dict): Log entry data containing 'text' and 'level' fields
            execution_id (str): UUID of the execution to log for

        Returns:
            ExecutionLog: Created log entry object

        Raises:
            Exception: If required fields missing
            ExecutionNotFound: If execution doesn't exist
        """
        logger.info("[SERVICE]: Creating execution log")
        text = log.get("text", None)
        level = log.get("level", None)
        if text is None or level is None:
            raise Exception
        execution = ExecutionService.get_execution(execution_id=execution_id)
        if not execution:
            raise ExecutionNotFound(
                message="Execution with id " + execution_id + " does not exist"
            )
        execution_log = ExecutionLog(text=text, level=level, execution_id=execution.id)
        try:
            logger.info("[DB]: ADD")
            db.session.add(execution_log)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return execution_log

    @staticmethod
    def get_execution_logs(execution_id, start_date, last_id):
        """
        Retrieve execution logs with optional filtering.

        Args:
            execution_id (str): UUID of the execution
            start_date (datetime, optional): Filter logs after this date
            last_id (int, optional): Filter logs after this log ID

        Returns:
            list: List of ExecutionLog objects

        Raises:
            ExecutionNotFound: If execution doesn't exist
        """
        logger.info(f"[SERVICE]: Getting execution logs of execution {execution_id}: ")
        logger.info("[DB]: QUERY")
        try:
            execution = ExecutionService.get_execution(execution_id=execution_id)
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        if not execution:
            raise ExecutionNotFound(
                message="Execution with id " + execution_id + " does not exist"
            )

        if start_date:
            logger.debug(start_date)
            return (
                ExecutionLog.query.filter(
                    ExecutionLog.execution_id == execution.id,
                    ExecutionLog.register_date > start_date,
                )
                .order_by(ExecutionLog.register_date)
                .all()
            )
        if last_id:
            return (
                ExecutionLog.query.filter(
                    ExecutionLog.execution_id == execution.id, ExecutionLog.id > last_id
                )
                .order_by(ExecutionLog.register_date)
                .all()
            )
        return execution.logs

    @staticmethod
    def cancel_execution(execution_id):
        """
        Cancel an execution and any associated Google Earth Engine tasks.

        This method performs a comprehensive cancellation process:
        1. Stops Docker containers/services via Celery task
        2. Cancels any Google Earth Engine tasks found in execution logs
        3. Updates execution status to CANCELLED with detailed logging
        4. Creates status log entries for the transition

        Args:
            execution_id (str): UUID of the execution to cancel

        Returns:
            dict: Cancellation results including execution data and detailed
                  cancellation information for Docker and GEE resources

        Raises:
            ExecutionNotFound: If execution doesn't exist
            Exception: If execution is already in terminal state or other errors
        """
        logger.info(f"[SERVICE]: Canceling execution {execution_id}")

        from gefapi.services.gee_service import GEEService

        try:
            # Get the execution
            execution = ExecutionService.get_execution(execution_id=execution_id)
            if not execution:
                raise ExecutionNotFound(
                    message="Execution with id " + execution_id + " does not exist"
                )

            # Check if execution is in a cancellable state
            if execution.status in ["FINISHED", "FAILED", "CANCELLED"]:
                raise Exception(f"Cannot cancel execution in {execution.status} state")

            cancellation_results = {
                "execution_id": execution.id,
                "previous_status": execution.status,
                "docker_service_stopped": False,
                "docker_container_stopped": False,
                "gee_tasks_cancelled": [],
                "errors": [],
            }

            # 1. Stop Docker service/container using Celery task
            try:
                if not celery_app:
                    raise ImportError("Celery app not available")

                logger.info(
                    f"[SERVICE]: Dispatching Docker cancellation task for "
                    f"execution {execution.id}"
                )
                # Send task to build queue where Docker access is available
                task_result = celery_app.send_task(
                    "docker.cancel_execution",
                    args=[execution.id],
                    queue="build",  # Use build queue for Docker access
                )

                # Wait for Docker cancellation to complete with timeout
                docker_results = task_result.get(timeout=60)  # 1 minute timeout

                # Merge Docker cancellation results
                cancellation_results["docker_service_stopped"] = docker_results.get(
                    "docker_service_stopped", False
                )
                cancellation_results["docker_container_stopped"] = docker_results.get(
                    "docker_container_stopped", False
                )
                cancellation_results["errors"].extend(docker_results.get("errors", []))

                logger.info(
                    f"[SERVICE]: Docker cancellation completed for "
                    f"execution {execution.id}"
                )

            except Exception as docker_error:
                error_msg = f"Docker cancellation task failed: {str(docker_error)}"
                logger.error(f"[SERVICE]: {error_msg}")
                cancellation_results["errors"].append(error_msg)
                rollbar.report_exc_info()

            # 2. Get execution logs to scan for GEE task IDs
            try:
                logs = (
                    ExecutionLog.query.filter(ExecutionLog.execution_id == execution.id)
                    .order_by(ExecutionLog.register_date)
                    .all()
                )
                log_texts = [log.text for log in logs if log.text]

                if log_texts:
                    logger.info(
                        f"[SERVICE]: Scanning {len(log_texts)} log entries for "
                        f"GEE task IDs"
                    )
                    gee_results = GEEService.cancel_gee_tasks_from_execution(log_texts)
                    cancellation_results["gee_tasks_cancelled"] = gee_results

                    # Log GEE cancellation results with better error categorization
                    for gee_result in gee_results:
                        if gee_result["success"]:
                            logger.info(
                                f"[SERVICE]: Successfully cancelled GEE task "
                                f"{gee_result['task_id']}"
                            )
                        else:
                            # Categorize error types for better logging
                            error_msg = gee_result.get("error", "Unknown error")
                            if "permission" in error_msg.lower():
                                logger.warning(
                                    f"[SERVICE]: Permission denied for GEE task "
                                    f"{gee_result['task_id']} - service account may "
                                    "lack earthengine.operations.get/cancel "
                                    "permissions"
                                )
                                # Don't add permission errors to main
                                # error list to avoid noise
                            else:
                                # Add all other errors (including "not found")
                                # to errors list
                                if "not found" in error_msg.lower():
                                    logger.info(
                                        f"[SERVICE]: GEE task {gee_result['task_id']} "
                                        f"not found - may have already completed"
                                    )
                                else:
                                    logger.warning(
                                        f"[SERVICE]: Failed to cancel GEE task "
                                        f"{gee_result['task_id']}: {error_msg}"
                                    )
                                cancellation_results["errors"].append(
                                    f"GEE task {gee_result['task_id']}: {error_msg}"
                                )
                else:
                    logger.info("[SERVICE]: No logs found to scan for GEE task IDs")
            except Exception as gee_error:
                logger.error(
                    f"[SERVICE]: Error processing GEE task cancellation: {gee_error}"
                )
                cancellation_results["errors"].append(
                    f"GEE task cancellation error: {str(gee_error)}"
                )
                rollbar.report_exc_info()

            # 3. Update execution status to CANCELLED using helper function
            try:
                # Add cancellation log entry
                cancellation_summary = []
                if cancellation_results["docker_service_stopped"]:
                    cancellation_summary.append("Docker service stopped")
                if cancellation_results["docker_container_stopped"]:
                    cancellation_summary.append("Docker container stopped")
                if cancellation_results["gee_tasks_cancelled"]:
                    successful_cancellations = len(
                        [
                            r
                            for r in cancellation_results["gee_tasks_cancelled"]
                            if r["success"]
                        ]
                    )
                    cancellation_summary.append(
                        f"{successful_cancellations}/"
                        f"{len(cancellation_results['gee_tasks_cancelled'])} "
                        f"GEE tasks cancelled"
                    )

                summary_text = (
                    "; ".join(cancellation_summary)
                    if cancellation_summary
                    else "No active resources found to cancel."
                )
                log_text = f"Execution cancelled by user. {summary_text}"
                if cancellation_results["errors"]:
                    error_text = "; ".join(cancellation_results["errors"][:3])
                    log_text += f" Errors: {error_text}"  # Limit error details

                cancellation_log = ExecutionLog(
                    text=log_text, level="INFO", execution_id=execution.id
                )

                # Use the helper function to update status and create status logs
                # Include the cancellation log in the same transaction
                update_execution_status_with_logging(
                    execution, "CANCELLED", additional_objects=[cancellation_log]
                )

                logger.info(
                    f"[SERVICE]: Successfully cancelled execution {execution.id}"
                )

            except Exception as db_error:
                logger.error(
                    f"[SERVICE]: Failed to update execution status: {db_error}"
                )
                rollbar.report_exc_info()
                raise db_error

            return {
                "execution": execution.serialize(),
                "cancellation_details": cancellation_results,
            }

        except Exception as error:
            logger.error(
                f"[SERVICE]: Error cancelling execution {execution_id}: {error}"
            )
            rollbar.report_exc_info()
            raise error
