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
        "gefapi.services.docker_service.docker_run": {"queue": "build"},
        "docker.get_service_logs": {"queue": "build"},
        "gefapi.tasks.status_monitoring.refresh_swarm_cache_task": {"queue": "build"},
        # Route all scheduled tasks with Docker access to build queue
        "gefapi.tasks.execution_cleanup.cleanup_stale_executions": {"queue": "build"},
        "gefapi.tasks.execution_cleanup.cleanup_finished_executions": {
            "queue": "build"
        },
        "gefapi.tasks.execution_cleanup.cleanup_old_failed_executions": {
            "queue": "build"
        },
        "gefapi.tasks.refresh_token_cleanup.cleanup_expired_refresh_tokens": {
            "queue": "default"
        },
        "gefapi.tasks.docker_service_monitoring.monitor_failed_docker_services": {
            "queue": "build"
        },
        "gefapi.tasks.docker_completed_monitoring.monitor_completed_docker_services": {
            "queue": "build"
        },
        # Stats cache refresh tasks - run on default queue
        "gefapi.tasks.stats_cache_refresh.refresh_dashboard_stats_cache": {
            "queue": "default"
        },
        "gefapi.tasks.stats_cache_refresh.refresh_execution_stats_cache": {
            "queue": "default"
        },
        "gefapi.tasks.stats_cache_refresh.refresh_user_stats_cache": {
            "queue": "default"
        },
        "gefapi.tasks.stats_cache_refresh.warmup_stats_cache_on_startup": {
            "queue": "default"
        },
        # All other tasks use default queue
    }

    # Configure periodic tasks
    celery.conf.beat_schedule = {
        "refresh-swarm-cache": {
            "task": "gefapi.tasks.status_monitoring.refresh_swarm_cache_task",
            "schedule": 120.0,  # Every 2 minutes (120 seconds)
            "options": {"queue": "build"},  # Run on build queue with Docker access
        },
        "cleanup-stale-executions": {
            "task": "gefapi.tasks.execution_cleanup.cleanup_stale_executions",
            "schedule": 3600.0,  # Every hour (3600 seconds)
            "options": {"queue": "build"},  # Run on build queue with Docker access
        },
        "cleanup-finished-executions": {
            "task": "gefapi.tasks.execution_cleanup.cleanup_finished_executions",
            "schedule": 86400.0,  # Every day (86400 seconds)
            "options": {"queue": "build"},  # Run on build queue with Docker access
        },
        "cleanup-old-failed-executions": {
            "task": "gefapi.tasks.execution_cleanup.cleanup_old_failed_executions",
            "schedule": 86400.0,  # Every day (86400 seconds)
            "options": {"queue": "build"},  # Run on build queue with Docker access
        },
        "cleanup-expired-refresh-tokens": {
            "task": "gefapi.tasks.refresh_token_cleanup.cleanup_expired_refresh_tokens",
            "schedule": 86400.0,  # Every day (86400 seconds)
        },
        "monitor-failed-docker-services": {
            "task": (
                "gefapi.tasks.docker_service_monitoring.monitor_failed_docker_services"
            ),
            "schedule": 120.0,  # Every 2 minutes (120 seconds) - balanced detection
            "options": {"queue": "build"},  # Run on build queue with Docker access
        },
        "monitor-completed-docker-services": {
            "task": (
                "gefapi.tasks.docker_completed_monitoring.monitor_completed_docker_services"
            ),
            "schedule": 180.0,  # Every 3 minutes - check for completed services
            "options": {"queue": "build"},  # Run on build queue with Docker access
        },
        # Stats cache refresh tasks for performance optimization
        "refresh-dashboard-stats-cache": {
            "task": "gefapi.tasks.stats_cache_refresh.refresh_dashboard_stats_cache",
            "schedule": 240.0,  # Every 4 minutes (cache TTL is 5 minutes)
            "options": {"queue": "default"},  # Run on default queue
        },
        "refresh-execution-stats-cache": {
            "task": "gefapi.tasks.stats_cache_refresh.refresh_execution_stats_cache",
            "schedule": 300.0,  # Every 5 minutes
            "options": {"queue": "default"},  # Run on default queue
        },
        "refresh-user-stats-cache": {
            "task": "gefapi.tasks.stats_cache_refresh.refresh_user_stats_cache",
            "schedule": 360.0,  # Every 6 minutes
            "options": {"queue": "default"},  # Run on default queue
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
