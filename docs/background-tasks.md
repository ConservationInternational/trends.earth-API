# Background Tasks

The API uses Celery for background task processing. All periodic tasks are scheduled
automatically by the Celery beat scheduler.

## Periodic Tasks

| Task | Schedule | Purpose |
|------|----------|---------|
| `monitor_failed_docker_services` | Every 2 minutes | Checks whether Docker services exist for PENDING/READY/RUNNING executions. Marks executions as FAILED if no service is found (after the dispatch grace period). |
| `monitor_completed_docker_services` | Every 3 minutes | Removes lingering Docker services for executions already marked FINISHED or FAILED in the database. |
| `process_queued_executions` | Every 30 seconds | Dispatches queued executions when concurrency slots become available. |
| `cleanup_stale_executions` | Every hour | Marks executions that have been RUNNING for more than 3 days as FAILED. |
| `cleanup_finished_executions` | Daily | Removes Docker services for executions that have already completed. |
| `cleanup_old_failed_executions` | Daily | Removes Docker services for executions that failed more than 14 days ago. |
| `monitor_batch_executions` | Every 2 minutes | Polls AWS Batch for status changes on executions dispatched via Batch. |
| `monitor_openeo_jobs` | Every 60 seconds | Polls openEO backends for status changes on openEO executions. |
| `refresh_swarm_cache_task` | Every 2 minutes | Refreshes the cached Docker Swarm node/service status used for cluster monitoring. |
| `cleanup_expired_refresh_tokens` | Daily | Removes expired refresh tokens from the database. |
| `cleanup_inactive_refresh_tokens` | Daily | Revokes refresh tokens that have been inactive for 14+ days. |
| `cleanup_unverified_users` | Weekly | Removes user accounts that were never email-verified. |
| `cleanup_never_logged_in_users` | Weekly | Removes user accounts that registered but never logged in. |
| `cleanup_expired_email_hashes` | Daily | GDPR compliance: removes expired hashed emails from the deletion audit log. |
| `refresh_dashboard_stats_cache` | Every 4 minutes | Pre-computes dashboard statistics for fast API responses. |

> **Status log entries** are created event-driven (on each execution status change), not by a periodic task. There is no `collect_system_metrics` scheduled task.

## Common Failure Pattern: "Docker service not found"

**Symptom:** Execution log contains only:
> `Cancelled by celery task 'monitor_failed_docker_services' - Docker service not found.`

**Cause:** The `docker_run` Celery task was delayed (queue backlog or resource contention).
The monitor ran its 2-minute cycle before the Docker service was created, so it cancelled
the execution prematurely.

A 3-minute dispatch grace period (`DISPATCH_GRACE_PERIOD_SECONDS = 180`) is applied after
`dispatched_at` is set: the monitor will not cancel an execution whose service is missing
if it was dispatched fewer than 3 minutes ago. If the grace period has elapsed and still
no Docker service exists, the execution is marked FAILED.

**Diagnosis checklist:**
- Job has zero script-level logs (only the monitor error) → service was never created
- Job duration is ~30–120 seconds (killed on the next monitor cycle)
- Same script/user has successful runs with normal duration
- `start_date` ≈ `queued_at` (not stuck in queue)

**Related code:**
- `gefapi/tasks/docker_service_monitoring.py` — the monitor task
- `gefapi/services/docker_service.py` `docker_run()` — creates Docker services

## Common Failure Pattern: "Docker service detected in restart loop"

**Symptom:** Execution log contains:
> `Cancelled by celery task 'monitor_failed_docker_services' - Docker service detected in restart loop or failed state.`

**Cause:** The Docker service was created but the container kept crashing. After 2+ failed
task attempts (`RESTART_LOOP_THRESHOLD`), the monitor marks it as failed.

**Diagnosis:** Check for script bugs, missing dependencies, or resource limits.

## Celery Configuration

Workers are configured via environment variables:

- `REDIS_URL` — Redis connection string for Celery broker and result backend
- `CELERY_WORKER_CONCURRENCY` — Number of concurrent worker processes (default: 4)

Start workers via Docker Compose:

```bash
# Start worker
docker compose -f docker-compose.develop.yml run --rm api worker

# Start beat scheduler (required for periodic tasks)
docker compose -f docker-compose.develop.yml run --rm api beat
```
