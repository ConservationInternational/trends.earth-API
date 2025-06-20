"""SCRIPT SERVICE"""

from __future__ import absolute_import, division, print_function

import datetime
import logging
from uuid import UUID

import rollbar

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import ExecutionNotFound, ScriptNotFound, ScriptStateNotValid
from gefapi.models import Execution, ExecutionLog
from gefapi.services import EmailService, ScriptService, UserService, docker_run

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
"Datasets" tab in the Trends.Earth plugin window.<\p>
<p>Thank you, </br>The Trends.Earth team</p>
"""


def dict_to_query(params):
    query = ""
    for key in params.keys():
        query += key + "=" + params.get(key) + "&"
    return query[0:-1]


class ExecutionService(object):
    """Execution Class"""

    @staticmethod
    def get_executions(
        user,
        target_user_id=None,
        updated_at=None,
        status=None,
        start_date_gte=None,
        start_date_lte=None,
        end_date_gte=None,
        end_date_lte=None,
        sort=None,
        page=1,
        per_page=2000,
    ):
        logger.info("[SERVICE]: Getting executions")
        logger.info("[DB]: QUERY")
        query = None
        if page < 1:
            raise Exception("Page must be greater than 0")
        if per_page < 1:
            raise Exception("Per page must be greater than 0")

        # Admin
        if user.role == "ADMIN":
            # Target User
            if target_user_id:
                try:
                    UUID(target_user_id, version=4)
                except Exception as error:
                    rollbar.report_exc_info()
                    raise error
                query = db.session.query(Execution).filter(
                    Execution.user_id == target_user_id
                )
            # All
            else:
                query = Execution.query
        # ME
        else:
            query = db.session.query(Execution).filter(Execution.user_id == user.id)

        if status:
            query = query.filter(Execution.status == status)
        if start_date_gte:
            query = query.filter(Execution.start_date >= start_date_gte)
        if start_date_lte:
            query = query.filter(Execution.start_date <= start_date_lte)
        if end_date_gte:
            query = query.filter(Execution.end_date >= end_date_gte)
        if end_date_lte:
            query = query.filter(Execution.end_date <= end_date_lte)
        elif updated_at:
            # For backwards compatibility, if no end_date_lte is provided,
            # filter by updated_at
            query = query.filter(Execution.end_date >= updated_at)

        if sort:
            sort_field = sort[1:] if sort.startswith("-") else sort
            sort_direction = "desc" if sort.startswith("-") else "asc"
            if hasattr(Execution, sort_field):
                query = query.order_by(
                    getattr(getattr(Execution, sort_field), sort_direction)()
                )
        else:
            # Default to sorting by end_date for backwards compatibility
            query = query.order_by(Execution.end_date.desc())

        total = query.count()
        executions = query.offset((page - 1) * per_page).limit(per_page).all()
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
        logger.info("[SERVICE]: Getting execution " + execution_id)
        logger.info("[DB]: QUERY")
        # user = 'from service' just in case the requests comes from the service
        if user == "fromservice" or user.role == "ADMIN":
            try:
                UUID(execution_id, version=4)
                execution = Execution.query.filter_by(id=execution_id).first()
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        else:
            try:
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
        logger.info(
            "[SERVICE]: Getting execution logs of execution %s: " % (execution_id)
        )
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
        elif last_id:
            return (
                ExecutionLog.query.filter(
                    ExecutionLog.execution_id == execution.id, ExecutionLog.id > last_id
                )
                .order_by(ExecutionLog.register_date)
                .all()
            )
        else:
            return execution.logs
