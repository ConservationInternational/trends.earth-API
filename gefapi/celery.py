import os

from celery import Celery
from celery.signals import task_failure

import rollbar

rollbar.init(os.getenv('ROLLBAR_SERVER_TOKEN'), os.getenv('ENV'))


def celery_base_data_hook(request, data):
    data['framework'] = 'celery'

rollbar.BASE_DATA_HOOK = celery_base_data_hook


@task_failure.connect
def handle_task_failure(**kw):
    rollbar.report_exc_info(extra_data=kw)


def make_celery(app):
    celery = Celery(app.import_name, backend=app.config['result_backend'],
                    broker=app.config['broker_url'])
    celery.conf.update(app.config)
    TaskBase = celery.Task
    class ContextTask(TaskBase):
        abstract = True
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery
