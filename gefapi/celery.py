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

    # --- Worker resilience settings ---
    # Acknowledge tasks AFTER execution completes, not on receipt.
    # If a worker crashes mid-task (e.g. node failure), the message
    # stays in the broker and is re-delivered to another worker.
    celery.conf.task_acks_late = True

    # Re-queue the task if the worker process is lost (OOM kill,
    # SIGKILL, node shutdown) instead of acknowledging it as failed.
    celery.conf.task_reject_on_worker_lost = True

    # Only prefetch one task at a time per worker process.  With
    # late-ack enabled this prevents a crashing worker from losing
    # multiple buffered tasks that were already pulled from the broker.
    celery.conf.worker_prefetch_multiplier = 1

    # Recycle worker processes after N tasks to prevent memory leaks
    # from accumulating SQLAlchemy sessions, Docker client objects,
    # and Python heap fragmentation.  Without this, long-running
    # prefork workers steadily grow until the container OOM-kills them
    # (signal 9 / SIGKILL).
    celery.conf.worker_max_tasks_per_child = 50

    # Hard per-child memory ceiling (in KB).  If a single task causes
    # a worker to exceed this, Celery replaces the process *before*
    # the container runtime OOM-kills it — which avoids losing the
    # task when task_reject_on_worker_lost is True.
    # 350 MB ≈ 358400 KB — leaves headroom within the 1.5 GB container
    # limit when running with the default concurrency.
    celery.conf.worker_max_memory_per_child = 358400  # KB

    # Configure task routing - build tasks go to build queue
    celery.conf.task_routes = {
        "gefapi.services.docker_service.docker_build": {"queue": "build"},
        "gefapi.services.docker_service.docker_run": {"queue": "build"},
        "docker.get_service_logs": {"queue": "build"},
        "gefapi.tasks.status_monitoring.refresh_swarm_cache_task": {"queue": "build"},
        "gefapi.tasks.status_monitoring.warm_swarm_cache_on_startup": {
            "queue": "build"
        },
        # Route all scheduled tasks with Docker access to build queue
        "gefapi.tasks.execution_cleanup.cleanup_stale_executions": {"queue": "build"},
        "gefapi.tasks.execution_cleanup.cleanup_finished_executions": {
            "queue": "build"
        },
        "gefapi.tasks.execution_cleanup.cleanup_old_failed_executions": {
            "queue": "build"
        },
        "gefapi.tasks.execution_cancellation.cancel_execution_workflow": {
            "queue": "build"
        },
        "gefapi.tasks.refresh_token_cleanup.cleanup_expired_refresh_tokens": {
            "queue": "default"
        },
        "gefapi.tasks.refresh_token_cleanup.cleanup_inactive_refresh_tokens": {
            "queue": "default"
        },
        "gefapi.tasks.user_cleanup.cleanup_unverified_users": {"queue": "default"},
        "gefapi.tasks.user_cleanup.cleanup_never_logged_in_users": {"queue": "default"},
        "gefapi.tasks.user_cleanup.get_user_cleanup_stats": {"queue": "default"},
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
        # Batch monitoring – no Docker access needed, runs on default queue
        "gefapi.tasks.batch_monitoring.monitor_batch_executions": {"queue": "default"},
        # openEO monitoring – polls openEO backends, no Docker access needed
        "gefapi.tasks.openeo_monitoring.monitor_openeo_jobs": {"queue": "default"},
        # Batch dispatch task – submit jobs to AWS Batch (no Docker needed)
        "gefapi.services.batch_service.batch_run": {"queue": "default"},
        # Execution queue processor – dispatches queued executions (no Docker needed)
        "gefapi.tasks.queue_processor.process_queued_executions": {"queue": "default"},
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
        "cleanup-inactive-refresh-tokens": {
            "task": (
                "gefapi.tasks.refresh_token_cleanup.cleanup_inactive_refresh_tokens"
            ),
            "schedule": 86400.0,  # Every day (86400 seconds)
        },
        # User cleanup tasks - run weekly for safety
        "cleanup-unverified-users": {
            "task": "gefapi.tasks.user_cleanup.cleanup_unverified_users",
            "schedule": 604800.0,  # Every week (7 days = 604800 seconds)
        },
        "cleanup-never-logged-in-users": {
            "task": "gefapi.tasks.user_cleanup.cleanup_never_logged_in_users",
            "schedule": 604800.0,  # Every week (7 days = 604800 seconds)
        },
        # GDPR compliance - clear expired email hashes from deletion audit
        "cleanup-expired-email-hashes": {
            "task": "gefapi.tasks.deletion_audit_cleanup.cleanup_expired_email_hashes",
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
        # Batch execution monitoring – poll AWS Batch for status changes
        "monitor-batch-executions": {
            "task": "gefapi.tasks.batch_monitoring.monitor_batch_executions",
            "schedule": 120.0,  # Every 2 minutes
            "options": {"queue": "default"},
        },
        # openEO execution monitoring – poll openEO backends for status changes
        "monitor-openeo-jobs": {
            "task": "gefapi.tasks.openeo_monitoring.monitor_openeo_jobs",
            "schedule": 60.0,  # Every 60 seconds
            "options": {"queue": "default"},
        },
        # Execution queue processor – dispatch queued executions when slots available
        "process-queued-executions": {
            "task": "gefapi.tasks.queue_processor.process_queued_executions",
            "schedule": 30.0,  # Every 30 seconds for responsive queue processing
            "options": {"queue": "default"},
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
