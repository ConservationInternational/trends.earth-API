# Data Models

## Script

```python
{
    "id": "UUID",
    "name": "string",
    "slug": "string (unique)",
    "description": "string",
    "created_at": "datetime",
    "updated_at": "datetime",
    "user_id": "UUID",
    "status": "string",           # PENDING, UPLOADED, SUCCESS, FAILED
    "public": "boolean",
    "restricted": "boolean",
    "allowed_roles": ["string"],
    "allowed_users": ["UUID"],
    "cpu_reservation": "integer",
    "cpu_limit": "integer",
    "memory_reservation": "integer",
    "memory_limit": "integer",
    "environment": "string",
    "environment_version": "string",
    "compute_type": "string",     # gee, openeo, batch
    "uses_gee": "boolean",
    "build_error": "string",      # populated on build failure
    # Batch-specific (compute_type == "batch" only)
    "batch_job_definition": "string",
    "batch_job_queue": "string",
    "batch_image": "string",
    # openEO-specific
    "openeo_backend_url": "string"
}
```

## Execution

```python
{
    "id": "UUID",
    "start_date": "datetime",
    "end_date": "datetime",
    "status": "string",           # PENDING, READY, RUNNING, FINISHED, FAILED, CANCELLED, CANCELLING
    "progress": "integer",        # 0-100
    "params": "object",
    "results": "object",
    "script_id": "UUID",
    "user_id": "UUID",
    "queued_at": "datetime",      # set when queued due to concurrency limit
    "dispatched_at": "datetime",  # set when docker_run Celery task starts processing
    "duration": "float"           # seconds, only when included via ?include=duration
}
```

### Execution Lifecycle

```
User submits → PENDING (queued_at set if over concurrent limit)
                 ↓ (queue_processor dispatches via Celery)
              PENDING (docker_run task picked up, dispatched_at set)
                 ↓ (Docker service created)
              READY
                 ↓ (container starts executing)
              RUNNING
                 ↓
              FINISHED or FAILED or CANCELLING → CANCELLED
```

Non-admin users default to a maximum of 3 concurrent executions (`MAX_CONCURRENT_PER_USER`).
Admin users bypass queueing.

## User

```python
{
    "id": "UUID",
    "created_at": "datetime",
    "email": "string (unique)",
    "role": "string",             # USER, ADMIN, SUPERADMIN
    "name": "string",
    "country": "string",
    "institution": "string",
    "max_concurrent_executions": "integer"
}
```

## Status Log

```python
{
    "id": "integer",
    "timestamp": "datetime",
    "executions_pending": "integer",
    "executions_ready": "integer",
    "executions_running": "integer",
    "executions_finished": "integer",
    "executions_failed": "integer",
    "executions_cancelled": "integer",
    # Set only for status-change events (null for periodic snapshots):
    "status_from": "string",
    "status_to": "string",
    "execution_id": "UUID"
}
```

Status logs are created automatically whenever an execution status changes (event-driven,
not periodic polling). Accessible via `GET /api/v1/status` (Admin+ only).
