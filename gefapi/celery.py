from celery import Celery
from celery.signals import task_failure
import rollbar


def celery_base_data_hook(request, data):
    data["framework"] = "celery"


rollbar.BASE_DATA_HOOK = celery_base_data_hook


@task_failure.connect
def handle_task_failure(**kw):
    rollbar.report_exc_info(extra_data=kw)


def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config["result_backend"],
        broker=app.config["broker_url"],
    )
    celery.conf.update(app.config)

    # Configure task routing - build tasks go to build queue
    celery.conf.task_routes = {
        "gefapi.services.docker_service.docker_build": {"queue": "build"},
        # All other tasks use default queue
    }

    # Configure periodic tasks
    celery.conf.beat_schedule = {
        "collect-system-status": {
            "task": "gefapi.tasks.status_monitoring.collect_system_status",
            "schedule": 120.0,  # Every 2 minutes (120 seconds)
        },
        "cleanup-stale-executions": {
            "task": "gefapi.tasks.execution_cleanup.cleanup_stale_executions",
            "schedule": 3600.0,  # Every hour (3600 seconds)
        },
        "cleanup-finished-executions": {
            "task": "gefapi.tasks.execution_cleanup.cleanup_finished_executions",
            "schedule": 86400.0,  # Every day (86400 seconds)
        },
        "cleanup-old-failed-executions": {
            "task": "gefapi.tasks.execution_cleanup.cleanup_old_failed_executions",
            "schedule": 86400.0,  # Every day (86400 seconds)
        },
        "cleanup-expired-refresh-tokens": {
            "task": "gefapi.tasks.refresh_token_cleanup.cleanup_expired_refresh_tokens",
            "schedule": 86400.0,  # Every day (86400 seconds)
        },
        "monitor-failed-docker-services": {
            "task": (
                "gefapi.tasks.docker_service_monitoring.monitor_failed_docker_services"
            ),
            "schedule": 600.0,  # Every 10 minutes (600 seconds)
        },
    }
    celery.conf.timezone = "UTC"

    task_base = celery.Task

    class ContextTask(task_base):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return task_base.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    return celery
