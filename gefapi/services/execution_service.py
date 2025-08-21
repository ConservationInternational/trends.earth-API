"""SCRIPT SERVICE"""

import datetime
import logging
from uuid import UUID

import rollbar
from sqlalchemy import case, func

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import ExecutionNotFound, ScriptNotFound, ScriptStateNotValid
from gefapi.models import Execution, ExecutionLog, Script, User
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
    query = ""
    for key in params:
        query += key + "=" + params.get(key) + "&"
    return query[0:-1]


class ExecutionService:
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
            # For admin, no additional user filter needed
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
            environment = SETTINGS.get("environment", {})
            environment["EXECUTION_ID"] = execution.id
            docker_run.delay(execution.id, script.slug, environment, params)
        except Exception as e:
            rollbar.report_exc_info()
            raise e
        return execution

    @staticmethod
    def get_execution(execution_id, user="fromservice"):
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
        if status is not None:
            execution.status = status
            if status == "FINISHED" or status == "FAILED":
                execution.end_date = datetime.datetime.utcnow()
                execution.progress = 100
                user = UserService.get_user(str(execution.user_id))
                script = ScriptService.get_script(str(execution.script_id))
                try:
                    EmailService.send_html_email(
                        recipients=[user.email],
                        html=EXECUTION_FINISHED_MAIL_CONTENT.format(
                            status,
                            execution.params.get("task_name"),
                            script.name,
                            str(execution.id),
                            execution.start_date,
                            execution.end_date,
                            status,
                        ),
                        subject="[trends.earth] Execution finished",
                    )
                except Exception:
                    rollbar.report_exc_info()
                    logger.info("Failed to send email - check email service")
        if progress is not None:
            execution.progress = progress
        if results is not None:
            execution.results = results
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
        """Cancel an execution and any associated Google Earth Engine tasks"""
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

                    # Log GEE cancellation results
                    for gee_result in gee_results:
                        if gee_result["success"]:
                            logger.info(
                                f"[SERVICE]: Successfully cancelled GEE task "
                                f"{gee_result['task_id']}"
                            )
                        else:
                            logger.warning(
                                f"[SERVICE]: Failed to cancel GEE task "
                                f"{gee_result['task_id']}: {gee_result['error']}"
                            )
                            cancellation_results["errors"].append(
                                f"GEE task {gee_result['task_id']}: "
                                f"{gee_result['error']}"
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

            # 3. Update execution status to CANCELLED
            try:
                execution.status = "CANCELLED"
                execution.end_date = datetime.datetime.utcnow()
                execution.progress = 100

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

                db.session.add(execution)
                db.session.add(cancellation_log)
                db.session.commit()

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
