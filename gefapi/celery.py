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

    # Configure periodic tasks
    celery.conf.beat_schedule = {
        "collect-system-status": {
            "task": "gefapi.tasks.status_monitoring.collect_system_status",
            "schedule": 120.0,  # Every 2 minutes (120 seconds)
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
