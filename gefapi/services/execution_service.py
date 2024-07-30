"""SCRIPT SERVICE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import logging
import os
from uuid import UUID

import rollbar

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.errors import ExecutionNotFound
from gefapi.errors import ScriptNotFound
from gefapi.errors import ScriptStateNotValid
from gefapi.models import Execution
from gefapi.models import ExecutionLog
from gefapi.services import docker_run
from gefapi.services import EmailService
from gefapi.services import ScriptService
from gefapi.services import UserService

rollbar.init(os.getenv("ROLLBAR_SERVER_TOKEN"), os.getenv("ENV"))

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
    def get_executions(user, target_user_id=None, updated_at=None):
        logging.info("[SERVICE]: Getting executions")
        logging.info("[DB]: QUERY")
        if not updated_at:
            updated_at = datetime.datetime(2000, 12, 1)
        # Admin
        if user.role == "ADMIN":
            # Target User
            if target_user_id:
                try:
                    val = UUID(target_user_id, version=4)
                except Exception as error:
                    rollbar.report_exc_info()
                    raise error
                executions = (
                    db.session.query(Execution)
                    .filter(Execution.user_id == target_user_id)
                    .filter(Execution.end_date > updated_at)
                    .order_by(Execution.end_date)
                )
            # All
            else:
                executions = (
                    Execution.query.filter(Execution.end_date > updated_at)
                    .order_by(Execution.end_date)
                    .all()
                )
            return executions
        # ME
        else:
            executions = (
                db.session.query(Execution)
                .filter(Execution.user_id == user.id)
                .filter(Execution.end_date > updated_at)
                .order_by(Execution.end_date)
            )
            return executions

    @staticmethod
    def create_execution(script_id, params, user):
        logging.info("[SERVICE]: Creating execution")
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
            logging.info("[DB]: ADD")
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
        logging.info("[SERVICE]: Getting execution " + execution_id)
        logging.info("[DB]: QUERY")
        # user = 'from service' just in case the requests comes from the service
        if user == "fromservice" or user.role == "ADMIN":
            try:
                val = UUID(execution_id, version=4)
                execution = Execution.query.filter_by(id=execution_id).first()
            except Exception as error:
                rollbar.report_exc_info()
                raise error
        else:
            try:
                val = UUID(execution_id, version=4)
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
        logging.info("[SERVICE]: Updating execution")
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
                    email = EmailService.send_html_email(
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
                except:
                    pass
        if progress is not None:
            execution.progress = progress
        if results is not None:
            execution.results = results
        try:
            logging.info("[DB]: ADD")
            db.session.add(execution)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return execution

    @staticmethod
    def create_execution_log(log, execution_id):
        logging.info("[SERVICE]: Creating execution log")
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
            logging.info("[DB]: ADD")
            db.session.add(execution_log)
            db.session.commit()
        except Exception as error:
            rollbar.report_exc_info()
            raise error
        return execution_log

    @staticmethod
    def get_execution_logs(execution_id, start_date, last_id):
        logging.info(
            "[SERVICE]: Getting execution logs of execution %s: " % (execution_id)
        )
        logging.info("[DB]: QUERY")
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
            logging.debug(start_date)
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
