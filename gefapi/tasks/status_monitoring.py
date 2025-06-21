"""STATUS MONITORING TASKS"""

from __future__ import absolute_import, division, print_function

import logging

import psutil
import rollbar
from celery import Task
from sqlalchemy import func

from gefapi import celery, db
from gefapi.models import Execution, Script, StatusLog, User

logger = logging.getLogger()


class StatusMonitoringTask(Task):
    """Base task for status monitoring"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Status monitoring task failed: {exc}")
        rollbar.report_exc_info()


@celery.task(base=StatusMonitoringTask, bind=True)
def collect_system_status(self):
    """Collect system status and save to status_log table"""
    logger.info("[TASK]: Collecting system status")

    try:
        # Count executions by status
        execution_counts = (
            db.session.query(Execution.status, func.count(Execution.id))
            .group_by(Execution.status)
            .all()
        )

        execution_status_map = dict(execution_counts)
        executions_active = execution_status_map.get("ACTIVE", 0)
        executions_ready = execution_status_map.get("READY", 0)
        executions_running = execution_status_map.get("RUNNING", 0)
        executions_finished = execution_status_map.get("FINISHED", 0)

        # Count users and scripts
        users_count = db.session.query(func.count(User.id)).scalar()
        scripts_count = db.session.query(func.count(Script.id)).scalar()

        # Get system metrics
        memory = psutil.virtual_memory()
        memory_available_percent = memory.available / memory.total * 100
        cpu_usage_percent = psutil.cpu_percent(interval=1)

        # Create status log entry
        status_log = StatusLog(
            executions_active=executions_active,
            executions_ready=executions_ready,
            executions_running=executions_running,
            executions_finished=executions_finished,
            users_count=users_count,
            scripts_count=scripts_count,
            memory_available_percent=memory_available_percent,
            cpu_usage_percent=cpu_usage_percent,
        )

        logger.info("[DB]: ADD")
        db.session.add(status_log)
        db.session.commit()

        logger.info(f"[TASK]: Status log created with ID {status_log.id}")
        return status_log.serialize()

    except Exception as error:
        logger.error(f"[TASK]: Error collecting system status: {str(error)}")
        rollbar.report_exc_info()
        db.session.rollback()
        raise error
