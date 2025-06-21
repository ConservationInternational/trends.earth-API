"""TASKS MODULE"""

from __future__ import absolute_import, division, print_function

# Import tasks to ensure they are registered with Celery
from gefapi.tasks import status_monitoring  # noqa: F401
