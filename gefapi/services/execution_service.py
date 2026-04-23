"""SCRIPT SERVICE"""

import datetime
import logging
import os
from uuid import UUID

import rollbar
from sqlalchemy import case, func

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import (
    ExecutionNotFound,
    GeeTermsRequiredError,
    ScriptNotFound,
    ScriptStateNotValid,
)
from gefapi.models import Execution, ExecutionLog, Script, StatusLog, User
from gefapi.services import (
    EmailService,
    ScriptService,
    UserService,
    batch_run,
    docker_run,
)
from gefapi.utils import mask_email
from gefapi.utils.permissions import is_admin_or_higher


def _get_user_active_execution_count(user_id):
    """Count executions in active states for a user.

    Active states are those that consume resources or are about to:
    - PENDING: About to start or waiting to be dispatched
    - READY: Container is ready/starting
    - RUNNING: Currently executing

    Note: This excludes PENDING executions that have queued_at set, as those
    are explicitly waiting in the queue and not consuming resources.

    Args:
        user_id: UUID of the user to count executions for

    Returns:
        int: Number of active executions for the user
    """
    return Execution.query.filter(
        Execution.user_id == user_id,
        Execution.status.in_(["PENDING", "READY", "RUNNING", "CANCELLING"]),
        # Exclude queued executions (PENDING with queued_at set)
        Execution.queued_at.is_(None),
    ).count()


def _dispatch_execution(execution_id, script_slug, environment, params, compute_type):
    """Dispatch an execution to the appropriate runner.

    Routes based on the per-script ``compute_type`` string rather than a
    system-wide orchestrator flag.  This allows multiple scripts with
    different compute backends to be dispatched simultaneously.

    Supported values:
    * ``"gee"`` (default) – run a GEE script in a Docker Swarm container.
    * ``"openeo"`` – run an openEO script in a Docker Swarm container, but
      submit the computation to an external openEO backend rather than GEE.
    * ``"batch"`` – submit to AWS Batch.

    Both ``"gee"`` and ``"openeo"`` use the Docker Swarm orchestrator.  The
    ``ORCHESTRATOR`` setting controls *how* the container is launched; the
    ``compute_type`` field controls *what* the container computes against.
    """
    if compute_type == "openeo":
        from gefapi.services.openeo_service import openeo_run

        openeo_run.delay(execution_id, script_slug, environment, params)
        return

    if compute_type == "batch":
        batch_run.delay(execution_id, script_slug, environment, params)
        return

    # Default: gee (compute_type == "gee" or unrecognised value) — runs via Docker Swarm
    orchestrator = SETTINGS.get("ORCHESTRATOR", "docker")
    if orchestrator == "docker":
        docker_run.delay(execution_id, script_slug, environment, params)
    else:
        raise ValueError(f"Unknown orchestrator: {orchestrator}")


# Security: Explicitly allowed fields for filter and sort operations
# to prevent unauthorized access to sensitive model fields
EXECUTION_ALLOWED_FILTER_FIELDS = {
    "id",
    "status",
    "progress",
    "start_date",
    "end_date",
    "script_id",
    "script_name",  # Via join
}
# Fields that require admin privileges to filter/sort
EXECUTION_ADMIN_ONLY_FIELDS = {"user_name", "user_email", "user_id"}
EXECUTION_ALLOWED_SORT_FIELDS = (
    EXECUTION_ALLOWED_FILTER_FIELDS | EXECUTION_ADMIN_ONLY_FIELDS | {"duration"}
)

# Import celery at module level for testing
try:
    from gefapi import celery as celery_app
except ImportError:
    celery_app = None

logger = logging.getLogger()

EXECUTION_FINISHED_MAIL_CONTENT = """
<p>Thank you for using Trends.Earth. The status of the below task is now
{status}. More details on this task are below:</p>
<ul>
    <li>Task name: {task_name}</li>
    <li>Script: {script_name}</li>
    <li>Task ID: {execution_id}</li>
    <li>Start time: {start_time}</li>
    <li>End time: {end_time}</li>
    <li>Status: {status}</li>
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
        # Track CANCELLING as in-progress work alongside RUNNING.
        executions_running = count_dict.get("RUNNING", 0) + count_dict.get(
            "CANCELLING", 0
        )
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
    def _is_batch_environment(script):
        """Return True if *script* should be dispatched via AWS Batch.

        A script is routed to Batch when its ``compute_type`` column is
        set to ``"batch"``.  This is fully data-driven – no environment
        names or slug prefixes are hard-coded here.
        """
        return (getattr(script, "compute_type", None) or "").lower() == "batch"

    @staticmethod
    def _build_execution_environment(user, execution_id, script=None):
        """
        Build environment variables for script execution container.

        Args:
            user: User model instance executing the script
            execution_id: ID of the execution
            script: Script model instance (optional). When present and the
                script uses a non-GEE compute path (``compute_type="batch"``),
                ``SKIP_GEE_INIT=true`` is set so the trends.earth-Environment
                entrypoint does not abort the container for missing GEE
                credentials.

        Returns:
            dict: Environment variables to pass to the container
        """
        # Start with base environment variables
        environment = SETTINGS.get("environment", {}).copy()
        environment["EXECUTION_ID"] = execution_id

        # Tell the Environment entrypoint to skip GEE credential validation
        # for scripts that do not need Earth Engine.  gefcore.runner also
        # checks REQUIRES_GEE on the script module, but entrypoint.sh runs
        # before Python and would otherwise hard-exit the container.
        if script and (getattr(script, "compute_type", None) or "").lower() == "batch":
            environment["SKIP_GEE_INIT"] = "true"

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
                    f"Adding OAuth credentials for user {mask_email(user.email)} "
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
                    masked = mask_email(user.email)
                    logger.info(f"OAuth environment variables added for user {masked}")
                else:
                    logger.warning(
                        f"User {mask_email(user.email)} has OAuth credential type "
                        "but missing tokens"
                    )

            elif user.gee_credentials_type == "service_account":
                # Add user's service account credentials
                logger.info(
                    f"Adding user service account credentials for user "
                    f"{mask_email(user.email)} execution {execution_id}"
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
                    masked = mask_email(user.email)
                    logger.info(
                        f"User service account credentials added for user {masked}"
                    )
                else:
                    masked = mask_email(user.email)
                    logger.warning(
                        f"User {masked} has service account credential type "
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

        # Inject openEO-specific environment variables when the script uses
        # the openEO compute type.  This allows the trends.earth-Environment
        # entrypoint to skip GEE initialisation and lets the openEO service
        # resolve credentials and the backend URL at dispatch time.
        compute_type = (getattr(script, "compute_type", None) or "gee").lower()
        if compute_type == "openeo":
            environment["SKIP_GEE_INIT"] = "true"

            # Inject per-user openEO credentials as JSON (plaintext; the
            # container environment is ephemeral and encrypted in transit).
            if user and user.has_openeo_credentials():
                import json as _json

                creds = user.get_openeo_credentials()
                if creds:
                    environment["OPENEO_CREDENTIALS"] = _json.dumps(creds)
                    logger.info(
                        "Injected per-user openEO credentials for execution %s",
                        execution_id,
                    )

            # Backend URL: per-script override → SETTINGS default.
            # Validate https:// before injecting into the container environment.
            from urllib.parse import urlparse

            backend_url = getattr(script, "openeo_backend_url", None) or SETTINGS.get(
                "OPENEO_DEFAULT_BACKEND_URL"
            )
            if backend_url:
                _parsed = urlparse(backend_url)
                if _parsed.scheme != "https" or not _parsed.netloc:
                    raise ValueError(
                        f"openEO backend URL must use https://, got: '{backend_url}'."
                    )
                environment["OPENEO_BACKEND_URL"] = backend_url

            # S3 output bucket / prefix for openEO job results
            output_bucket = SETTINGS.get("OUTPUT_S3_BUCKET")
            output_prefix = SETTINGS.get("OUTPUT_S3_PREFIX", "outputs")
            if output_bucket:
                environment["OUTPUT_S3_BUCKET"] = output_bucket
            if output_prefix:
                environment["OUTPUT_S3_PREFIX"] = output_prefix

        return environment

    """Execution Class"""

    @staticmethod
    def get_executions(
        user,
        target_user_id=None,
        updated_at=None,
        status=None,
        script_id=None,
        page=1,
        per_page=2000,
        paginate=True,
        filter_param=None,
        sort=None,
        include=None,
    ):
        """
        Retrieve executions with filtering, pagination, and permission controls.

        Args:
            user: User object for permission checking
            target_user_id (str, optional): Filter by specific user ID (admin only)
            updated_at (datetime, optional): Filter by start date
            status (str, optional): Filter by execution status
            script_id (str, optional): Filter by script UUID
            page (int): Page number for pagination (default: 1)
            per_page (int): Results per page (default: 2000, max: 2000)
            paginate (bool): Whether to apply pagination (default: True)
            filter_param (str, optional): SQL-style filter expressions
                (date comparisons like start_date>='2024-01-01' are supported)
            sort (str, optional): SQL-style sort expressions
            include (list, optional): Fields to include in serialization. When
                'user', 'user_name', 'user_email', 'script', or 'script_name'
                are present, eager loading is applied to avoid N+1 queries.

        Returns:
            tuple: (executions list, total count)

        Raises:
            Exception: If pagination parameters are invalid or filter permissions denied
        """
        from sqlalchemy.orm import joinedload

        from gefapi.utils.query_filters import parse_filter_param, parse_sort_param

        logger.info("[SERVICE]: Getting executions")
        logger.info("[DB]: QUERY")

        include = include or []

        # Validate pagination parameters only when pagination is requested
        if paginate:
            if page < 1:
                raise Exception("Page must be greater than 0")
            if per_page < 1:
                raise Exception("Per page must be greater than 0")

        query = db.session.query(Execution)

        # Eager-load relationships when include fields reference them
        needs_user = bool({"user", "user_name", "user_email"} & set(include))
        needs_script = bool({"script", "script_name"} & set(include))
        if needs_user:
            query = query.options(joinedload(Execution.user))
        if needs_script:
            query = query.options(joinedload(Execution.script))

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
        if script_id:
            try:
                if isinstance(script_id, UUID):
                    validated_script_id = script_id
                else:
                    validated_script_id = UUID(script_id, version=4)
            except Exception as error:
                rollbar.report_exc_info()
                raise error
            query = query.filter(Execution.script_id == validated_script_id)
        if status:
            query = query.filter(func.lower(Execution.status) == status.lower())
        if updated_at:
            # Filter by start_date since that's when executions begin
            # and is more reliable than end_date for ongoing executions
            query = query.filter(Execution.start_date >= updated_at)

        # Apply SQL-style filter_param if present (supports OR groups)
        # Date comparisons (e.g. start_date>='2024-01-01') are handled here.
        join_scripts = False
        join_users = False
        filter_clauses = []

        if filter_param:
            from sqlalchemy import and_

            all_allowed = EXECUTION_ALLOWED_FILTER_FIELDS | EXECUTION_ADMIN_ONLY_FIELDS

            def _resolve_filter_column(field_name):
                nonlocal join_scripts, join_users
                if field_name == "script_name":
                    join_scripts = True
                    return Script.name
                if field_name == "user_name":
                    if not is_admin_or_higher(user):
                        raise Exception(
                            "Only admin or superadmin users can filter by user_name"
                        )
                    join_users = True
                    return User.name
                if field_name == "user_email":
                    if not is_admin_or_higher(user):
                        raise Exception(
                            "Only admin or superadmin users can filter by user_email"
                        )
                    join_users = True
                    return User.email
                return getattr(Execution, field_name, None)

            filter_clauses = parse_filter_param(
                filter_param,
                allowed_fields=all_allowed,
                resolve_column=_resolve_filter_column,
                string_field_names={"script_name", "user_name", "user_email"},
            )

        # Apply SQL-style sorting if present
        order_clauses = []
        if sort:

            def _resolve_sort_column(field_name, direction):
                nonlocal join_scripts, join_users
                col = getattr(Execution, field_name, None)
                if col is not None:
                    # For end_date, handle NULLs explicitly so running
                    # executions (no end_date) always sort at the top
                    # when descending, and add start_date as a
                    # secondary sort for stable ordering.
                    if field_name == "end_date":
                        if direction == "desc":
                            ordered = Execution.end_date.desc().nulls_first()
                        else:
                            ordered = Execution.end_date.asc().nulls_last()
                        # Return a composite: end_date then start_date
                        return (
                            [
                                ordered,
                                Execution.start_date.desc(),
                            ],
                            True,
                        )
                    return col
                if field_name == "duration":
                    duration_expr = case(
                        (
                            Execution.end_date.isnot(None),
                            func.extract(
                                "epoch", Execution.end_date - Execution.start_date
                            ),
                        ),
                        else_=func.extract("epoch", func.now() - Execution.start_date),
                    )
                    ordered = (
                        duration_expr.desc()
                        if direction == "desc"
                        else duration_expr.asc()
                    )
                    return (ordered, True)
                if field_name == "script_name":
                    join_scripts = True
                    return Script.name
                if field_name == "user_email":
                    if not is_admin_or_higher(user):
                        raise Exception(
                            "Only admin or superadmin users can sort by user_email"
                        )
                    join_users = True
                    return User.email
                if field_name == "user_name":
                    if not is_admin_or_higher(user):
                        raise Exception(
                            "Only admin or superadmin users can sort by user_name"
                        )
                    join_users = True
                    return User.name
                return None

            order_clauses = parse_sort_param(
                sort,
                allowed_fields=EXECUTION_ALLOWED_SORT_FIELDS,
                resolve_column=_resolve_sort_column,
            )

        # Apply JOINs once (after both filter and sort have been processed)
        # to avoid duplicate joins when both reference the same table.
        if join_scripts:
            query = query.join(Script, Execution.script_id == Script.id)
        if join_users:
            query = query.join(User, Execution.user_id == User.id)

        # Apply filter clauses
        if filter_clauses:
            from sqlalchemy import and_

            query = query.filter(and_(*filter_clauses))

        # Apply sort clauses
        if order_clauses:
            for clause in order_clauses:
                if isinstance(clause, list):
                    for sub in clause:
                        query = query.order_by(sub)
                else:
                    query = query.order_by(clause)
        else:
            # Default: running executions (NULL end_date) first, then
            # most-recently-finished.  Among running executions, show
            # the most-recently-started first.
            query = query.order_by(
                Execution.end_date.desc().nulls_first(),
                Execution.start_date.desc(),
            )

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

        If the user has reached their concurrent execution limit, the execution
        is queued (status=PENDING with queued_at set) and will be dispatched
        later by the queue processor task. Admin/superadmin users are exempt
        from this limit.

        Args:
            script_id (str): UUID of the script to execute
            params (dict): Execution parameters and configuration
            user: User object creating the execution

        Returns:
            Execution: Created execution object (may be queued)

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

        # Enforce GEE terms acceptance for scripts that use GEE
        gee_enforcement = SETTINGS.get("GEE_TERMS_ENFORCEMENT_ENABLED", False)
        if (
            gee_enforcement
            and script.uses_gee
            and not is_admin_or_higher(user)
            and not user.gee_license_acknowledged
        ):
            raise GeeTermsRequiredError()

        execution = Execution(script_id=script.id, params=params, user_id=user.id)

        # Check if user should be queued (non-admin users over concurrent limit)
        queue_config = SETTINGS.get("EXECUTION_QUEUE", {})
        queue_enabled = queue_config.get("ENABLED", True)
        max_concurrent = queue_config.get("MAX_CONCURRENT_PER_USER", 3)

        # Per-user override takes precedence over global default
        if user.max_concurrent_executions is not None:
            max_concurrent = user.max_concurrent_executions

        should_queue = False
        if queue_enabled and not is_admin_or_higher(user):
            active_count = _get_user_active_execution_count(user.id)
            if active_count >= max_concurrent:
                should_queue = True
                execution.queued_at = datetime.datetime.now(datetime.UTC)
                logger.info(
                    f"[SERVICE]: User {user.id} has {active_count} active executions "
                    f"(limit: {max_concurrent}). Queueing execution."
                )

        try:
            logger.info("[DB]: ADD")
            db.session.add(execution)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error

        # If queued, don't dispatch yet - the queue processor will handle it
        if should_queue:
            logger.info(
                f"[SERVICE]: Execution {execution.id} queued for user {user.id}"
            )
            return execution

        # Dispatch immediately for admins or users under the limit
        try:
            environment = ExecutionService._build_execution_environment(
                user, execution.id, script=script
            )
            _dispatch_execution(
                execution.id,
                script.slug,
                environment,
                params,
                compute_type=(getattr(script, "compute_type", None) or "gee").lower(),
            )
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
                                status=status,
                                task_name=execution.params.get("task_name", "N/A"),
                                script_name=script.name,
                                execution_id=str(execution.id),
                                start_time=execution.start_date,
                                end_time=(
                                    execution.end_date or datetime.datetime.utcnow()
                                ),
                            ),
                            subject="[trends.earth] Execution finished",
                        )
                    except Exception:
                        rollbar.report_exc_info()
                        logger.info("Failed to send email - check email service")
                else:
                    masked = mask_email(user.email)
                    logger.info(
                        f"Email notification skipped for user {masked} - "
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
        Request cancellation for an execution asynchronously.

        This method validates the execution state, transitions the execution to
        CANCELLING, and dispatches a Celery task that performs cancellation work
        in the background.

        Args:
            execution_id (str): UUID of the execution to cancel

        Returns:
            dict: Execution data and cancellation dispatch metadata

        Raises:
            ExecutionNotFound: If execution doesn't exist
            Exception: If execution is already in terminal state or other errors
        """
        logger.info(f"[SERVICE]: Requesting cancellation for execution {execution_id}")

        try:
            # Get the execution
            execution = ExecutionService.get_execution(execution_id=execution_id)
            if not execution:
                raise ExecutionNotFound(
                    message="Execution with id " + execution_id + " does not exist"
                )

            # Check if execution is in a cancellable state
            if execution.status in ["FINISHED", "FAILED", "CANCELLED", "CANCELLING"]:
                raise Exception(f"Cannot cancel execution in {execution.status} state")

            previous_status = execution.status

            # Mark cancellation as in progress before cleaning up resources.
            cancellation_requested_log = ExecutionLog(
                text="Cancellation requested by user",
                level="INFO",
                execution_id=execution.id,
            )
            update_execution_status_with_logging(
                execution,
                "CANCELLING",
                additional_objects=[cancellation_requested_log],
                explicit_progress=execution.progress,
            )

            if not celery_app:
                raise ImportError("Celery app not available")

            try:
                task_result = celery_app.send_task(
                    "gefapi.tasks.execution_cancellation.cancel_execution_workflow",
                    args=[str(execution.id)],
                    queue="build",
                )
            except Exception:
                rollback_log = ExecutionLog(
                    text=(
                        "Cancellation dispatch failed; rolling back to previous status"
                    ),
                    level="ERROR",
                    execution_id=execution.id,
                )
                update_execution_status_with_logging(
                    execution,
                    previous_status,
                    additional_objects=[rollback_log],
                    explicit_progress=execution.progress,
                )
                raise

            logger.info(
                "[SERVICE]: Cancellation task %s queued for execution %s",
                task_result.id,
                execution.id,
            )

            return {
                "execution": execution.serialize(),
                "cancellation_details": {
                    "execution_id": execution.id,
                    "previous_status": previous_status,
                    "new_status": "CANCELLING",
                    "queued": True,
                    "task_id": str(task_result.id),
                    "errors": [],
                },
            }

        except Exception as error:
            logger.error(
                "[SERVICE]: Error requesting cancellation for execution %s: %s",
                execution_id,
                error,
            )
            rollbar.report_exc_info()
            raise error
