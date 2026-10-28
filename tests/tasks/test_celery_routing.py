"""Every Celery task must be routed to a queue that a worker consumes."""

import gefapi.services.openeo_service  # registers openeo_run
import gefapi.tasks  # noqa: F401  # registers all task modules

# Must match CELERY_WORKER_QUEUES in the docker-compose files.
CONSUMED_QUEUES = {"default", "build"}


def test_all_tasks_route_to_consumed_queue(app):
    from gefapi import celery

    task_names = [name for name in celery.tasks if name.startswith("gefapi.")]
    assert (
        "gefapi.tasks.app_access_notifications.notify_superadmins_of_app_access_request"
        in (task_names)
    )

    unconsumed = {
        name: queue
        for name in task_names
        if (queue := celery.amqp.router.route({}, name)["queue"].name)
        not in CONSUMED_QUEUES
    }
    assert not unconsumed, f"Tasks routed to queues no worker consumes: {unconsumed}"


def test_beat_schedule_queues_are_consumed(app):
    from gefapi import celery

    for entry_name, entry in celery.conf.beat_schedule.items():
        queue = entry.get("options", {}).get("queue")
        if queue is None:
            queue = celery.amqp.router.route({}, entry["task"])["queue"].name
        assert queue in CONSUMED_QUEUES, (
            f"Beat entry {entry_name} routed to unconsumed queue {queue}"
        )
