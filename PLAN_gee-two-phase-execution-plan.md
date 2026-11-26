# Three-Phase GEE Execution Plan

## Problem Statement

Today, every GEE script execution runs in a **long-lived container** that:

1. Builds an Earth Engine computation graph (~seconds)
2. Submits 1+ GEE export tasks to `ee.batch.Export.image.toCloudStorage()`
3. **Polls each task for completion** — 15 minutes to 48 hours, sleeping with exponential backoff (1.4s → 600s intervals)
4. Retrieves result URLs from the GCS bucket `ldmt`
5. Returns serialized results to the API

Steps 1-2 take seconds. Steps 3-4 take hours. The container sits idle for 99%+
of its lifetime, consuming a compute node, while waiting for Google's servers to
finish processing.

### Current architecture

```
Container starts
  → Download params from S3                    (gefcore/runner.py)
  → Initialize Earth Engine                    (gefcore/runner.py)
  → Call main.run(params, logger)              (script main.py)
    → Build ee.Image computation graph         (te_algorithms/gee/*.py)
    → TEImageV2.export(geojsons, ...)          (te_algorithms/gee/util.py)
      → For each geojson:
        → ee.batch.Export.image.toCloudStorage()  ← submits GEE task
        → gee_task thread starts polling          ← BLOCKS FOR HOURS
      → .join() all threads                       ← waits for all to complete
      → Query GCS bucket for result URLs
      → Serialize to JSON
  → send_result(result)                        (gefcore/api.py → PATCH /execution)
Container exits
```

Each `gee_task` instance is a `threading.Thread` in the container that:
- Calls `self.task.start()` to submit the GEE task
- Runs `poll_for_completion()` with `@backoff.on_predicate(backoff.expo, base=1.4, max_value=600, max_time=2880*60)` — polling GEE task status up to every 10 minutes for up to 48 hours
- After COMPLETED, calls `get_urls()` / `get_uris()` to query GCS for exported files

### Why this is a problem

| Issue | Impact |
|-------|--------|
| **Container runs for hours** | Holds a compute node hostage (t3.medium = $0.042/hr On-Demand) while doing nothing but `time.sleep()` |
| **Not Spot-safe** | If the node is reclaimed (2-min warning), the container dies. The GEE tasks keep running on Google's servers, but no one collects the results. The execution is FAILED. All GEE compute is wasted. |
| **No retry** | `backoff_limit=0` on the K8s Job. Container death = permanent failure. User must manually re-submit. |
| **Redundant polling** | Each container independently polls GEE and GCS. With 100 concurrent executions, that's 100 containers sleeping and polling. |
| **GEE API pressure** | Each container polls `ee.data.getTaskStatus()` independently, multiplying API calls by number of concurrent executions |

### Scope

This plan is **independent of the Kubernetes migration**. It can be implemented
on the current Docker Swarm infrastructure. However, it is a **prerequisite for
safe Spot usage** in the K8s architecture, since Spot nodes can be reclaimed at
any time and the current long-running container model cannot survive that.

---

## Target Architecture: Three-Phase Execution

Split GEE script execution into three short-lived phases — two containers
(seconds each) separated by a centralized polling loop on the API server:

```
Phase 1: SUBMIT (container, ~30 seconds)
  → Download params from S3
  → Initialize Earth Engine
  → Build ee.Image computation graph
  → Submit GEE export tasks
  → Store GEE task IDs + metadata in execution.results
  → Container exits                              ← freed in seconds, not hours
  → Status stays RUNNING

Phase 2: POLL (API server, Celery beat task)
  → Every 2 minutes, batch-query GEE task statuses for all active executions
  → When all tasks for an execution are COMPLETED:
    → Dispatch Phase 3 container
  → When any task FAILS:
    → Mark execution as FAILED with error details from GEE

Phase 3: FINALIZE (container, ~10 seconds)
  → Download params from S3 (same script image)
  → Initialize Earth Engine (for GCS access)
  → Call script's finalize() function
  → Collect result files from GCS bucket
  → Run any script-specific post-processing (combine, merge URIs, etc.)
  → Return final RasterResults JSON
  → Container exits with status FINISHED
```

**Why Phase 3 is a container, not Celery**: All result assembly and
post-processing logic lives in the GEE script code (`main.py` +
`te_algorithms`). Users can change result formatting by modifying scripts
only — no API code changes needed. The container runs the same script image
as Phase 1.

### Comparison

| Aspect | Current | Three-Phase |
|--------|---------|-------------|
| Container lifetime | 15 min - 48 hours | ~40 seconds (30s submit + 10s finalize) |
| Spot safety | ❌ Container death = lost results | ✅ GEE task IDs stored in DB; polling survives restarts |
| Node cost per execution | $0.01 - $2.00+ | ~$0.0006 (40s of t3.medium) |
| GEE API calls | N containers × polling interval | 1 batch query per poll cycle (all executions) |
| Retry on failure | Manual re-submit | Automatic — GEE tasks kept, just re-poll |
| Concurrent executions | N containers sleeping | 0 containers sleeping; 1 Celery task polling all |
| New statuses needed | — | None — stays PENDING → RUNNING → FINISHED |
| Plugin/schema changes | — | None — transparent to downstream consumers |

---

## Execution Types

Not all scripts use GEE. The architecture must differentiate:

### Script categorization

| Category | Scripts | Behavior |
|----------|---------|----------|
| **GEE export** (11 scripts) | productivity, land-cover, sdg-15-3-1-indicator, sdg-15-3-1-sub-indicators, soil-organic-carbon, total-carbon, restoration-biomass, download-data, urban-area, drought-vulnerability, landpks (partial) | Submit GEE tasks → poll → collect results from GCS |
| **GEE compute** (2 scripts) | time-series, sdg-15-3-1-stats | Use GEE's `reduceRegion()` / `getInfo()` synchronously — no export tasks, returns JSON inline |
| **Local compute** (1+ scripts) | unccd-sdg-15-3-1-error-recode, any `compute_type="batch"` scripts | Pure GDAL/local processing, no GEE dependency. `REQUIRES_GEE = False` / `SKIP_GEE_INIT=true` |

**Key insight**: Only the **GEE export** category benefits from three-phase
execution. The other categories already complete quickly or don't use GEE tasks
at all.

### New Script model field: `execution_mode`

```python
# gefapi/models/script.py — new column
execution_mode = db.Column(db.String(40), default="standard", nullable=False)
# Values:
#   "standard"  — current behavior: container runs to completion (GEE compute, local)
#   "gee_async" — two-phase: container submits GEE tasks, API polls for completion
```

This is independent of `compute_type` (which controls Docker vs Batch dispatch).
A GEE export script has `execution_mode="gee_async"` and `compute_type="docker"`.

---

## Phase 1: Submit (Container Changes)

### Changes to `te_algorithms/gee/util.py`

Add a new export mode that submits tasks but doesn't wait:

```python
# te_algorithms/gee/util.py — new method on TEImageV2


def export_async(
    self,
    geojsons,
    task_name,
    crs,
    logger,
    execution_id=None,
    proj=None,
    filetype=results.RasterFileType.COG,
    maxpixels=1e13,
):
    """Submit GEE export tasks and return task metadata WITHOUT waiting.

    Returns a dict suitable for the API to store and later pass to finalize():
    {
        "gee_tasks": [
            {
                "task_id": "ABCD1234...",
                "prefix": "exec123_productivity_1",
                "datatype": "int16",
                "bands": [{"name": "...", ...}, ...],
            },
            ...
        ],
        "task_name": "productivity",
        "bucket": "ldmt",
        "filetype": "COG",
    }
    """
    if not execution_id:
        execution_id = str(random.randint(1000000, 99999999))

    if not proj:
        proj = self.image.projection()

    scale = ee.Number(proj.nominalScale()).getInfo()

    gee_tasks = []
    n = 1

    for datatype, image in self.images.items():
        image.cast()

        for geojson in geojsons:
            if task_name:
                out_name = f"{execution_id}_{task_name}_{datatype.value}_{n}"
            else:
                out_name = f"{execution_id}_{datatype.value}_{n}"

            if filetype == results.RasterFileType.COG:
                as_COG = True
            else:
                as_COG = False

            export_params = {
                "image": image.image,
                "description": out_name,
                "fileNamePrefix": out_name,
                "bucket": BUCKET,
                "maxPixels": maxpixels,
                "crs": crs,
                "scale": scale,
                "region": get_coords(geojson),
                "formatOptions": {"cloudOptimized": as_COG},
            }

            task = ee.batch.Export.image.toCloudStorage(**export_params)
            task.start()

            gee_tasks.append(
                {
                    "task_id": task.status().get("id"),
                    "prefix": out_name,
                    "datatype": datatype.value,
                    "bands": [results.Band.Schema().dump(b) for b in image.bands],
                }
            )

            logger.debug(f"Submitted GEE task {task.status().get('id')} for {out_name}")
            n += 1

    return {
        "gee_tasks": gee_tasks,
        "task_name": task_name,
        "bucket": BUCKET,
        "filetype": filetype.value if hasattr(filetype, "value") else str(filetype),
    }


@staticmethod
def collect_results(gee_metadata):
    """Collect GCS files for completed GEE tasks and build RasterResults.

    Called by script finalize() functions in Phase 3. Replicates the
    URI-collection and serialization logic from export(), but without
    submitting or polling — tasks are already COMPLETED.

    Args:
        gee_metadata: The dict returned by export_async() and stored in
                      execution.results during Phase 1.

    Returns:
        Serialized RasterResults dict (same format as export()).
    """
    task_name = gee_metadata.get("task_name", "")
    filetype_str = gee_metadata.get("filetype", "COG")
    gee_tasks = gee_metadata.get("gee_tasks", [])

    # Group by datatype (same logic as export())
    output = {}
    for task_meta in gee_tasks:
        datatype = task_meta["datatype"]
        prefix = task_meta["prefix"]
        bands = task_meta["bands"]

        # Query GCS for exported files
        uris = _get_gcs_uris(prefix, gee_metadata.get("bucket", BUCKET))

        if datatype in output:
            output[datatype]["uris"].extend(uris)
        else:
            output[datatype] = {"uris": uris, "bands": bands}

    # Build RasterResults (same structure as export())
    rasters = {}
    for datatype, value in output.items():
        if len(value["uris"]) > 1:
            rasters[datatype] = TiledRaster.Schema().load(
                {
                    "tile_uris": value["uris"],
                    "bands": value["bands"],
                    "datatype": datatype,
                    "filetype": filetype_str,
                }
            )
        elif len(value["uris"]) == 1:
            rasters[datatype] = Raster.Schema().load(
                {
                    "uri": value["uris"][0],
                    "bands": value["bands"],
                    "datatype": datatype,
                    "filetype": filetype_str,
                }
            )

    gee_results = results.RasterResults(name=task_name, rasters=rasters, data={})
    return results.RasterResults.Schema().dump(gee_results)


def _get_gcs_uris(prefix, bucket=BUCKET):
    """Query GCS bucket for exported files matching a prefix."""
    import requests as http_requests

    resp = http_requests.get(
        f"https://www.googleapis.com/storage/v1/b/{bucket}/o?prefix={prefix}",
        timeout=120,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [
        results.URI.Schema().load(
            {
                "uri": item["mediaLink"],
                "etag": {"hash": item.get("md5Hash", ""), "type": "GCS_CRC32C"},
            }
        )
        for item in items
    ]
```

### Changes to `gefcore/runner.py`

The runner checks `EXECUTION_MODE` to determine which phase it's running:

```python
# gefcore/runner.py — modification to run()


def run():
    # ... existing init, param loading ...

    execution_mode = os.getenv("EXECUTION_MODE", "standard")

    if execution_mode == "gee_finalize":
        # Phase 3: script post-processes completed GEE results
        gee_metadata = json.loads(os.getenv("GEE_TASK_METADATA"))
        result = main.finalize(gee_metadata, params, logger)
        send_result(result)
    else:
        # Phase 1 (gee_async) or standard: run the script
        result = main.run(params, logger)

        if execution_mode == "gee_async":
            # Result is gee_tasks metadata — store it, keep status RUNNING
            patch_execution(json={"results": result})
            # Status stays RUNNING — the poller will detect gee_tasks in results
            # Trigger rapid-poll burst to catch fast-completing GEE tasks
            _trigger_rapid_polling()
        else:
            # Standard behavior — result is final
            send_result(result)
```

```python
def _trigger_rapid_polling():
    """Notify the API to schedule a rapid-poll burst.

    The container calls back to the API, which calls
    schedule_rapid_polling() to queue countdown-based polls.
    Alternatively, if the runner has broker access, it can call
    schedule_rapid_polling() directly.
    """
    try:
        requests.post(
            f"{API_URL}/api/v1/internal/gee-poll-burst",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            timeout=5,
        )
    except Exception:
        pass  # Best-effort; baseline beat polling will catch it
```

**Key detail**: Phase 1 does NOT change the execution status. It stays
`RUNNING`. The Celery poller identifies GEE-polling executions by checking
for `gee_tasks` in the execution's `results` JSONB — no new status needed.

### Changes to script `main.py` files (11 scripts)

Each GEE export script needs two changes:

**1. `run()` — use `export_async()` when in gee_async mode:**

```python
# BEFORE (e.g., land-cover/src/main.py — simple export):
return out.export(geojsons, "land_cover", crs, logger, EXECUTION_ID, proj)

# AFTER:
execution_mode = params.get("EXECUTION_MODE", "standard")
if execution_mode == "gee_async":
    return out.export_async(geojsons, "land_cover", crs, logger, EXECUTION_ID, proj)
else:
    return out.export(geojsons, "land_cover", crs, logger, EXECUTION_ID, proj)
```

**2. `finalize()` — new function for Phase 3 result collection:**

```python
# Simple scripts (6): default collect_results is sufficient
def finalize(gee_metadata, params, logger):
    """Phase 3: collect GCS results after GEE tasks complete."""
    from te_algorithms.gee.util import TEImageV2

    return TEImageV2.collect_results(gee_metadata)
```

Scripts that need post-processing override `finalize()` — see Phase 3 section
below.

This preserves backward compatibility — scripts work identically when
`EXECUTION_MODE` is not set or is `"standard"`.

---

## Phase 2: Poll (API Server Changes)

### No new execution status needed

The execution status stays `RUNNING` throughout Phases 1-3. The Celery poller
identifies GEE-polling executions by checking for `gee_tasks` in the
execution's `results` JSONB field. This means:

- **No changes to `te_schemas/jobs.py`** (no new `JobStatus` enum value)
- **No changes to the QGIS plugin** (no new status to handle in UI/manager)
- **No changes to the admin UI** (no new filter/display logic)
- **Fully transparent** to all downstream consumers

The `results` JSONB field serves double duty:
- During Phase 1→2: contains `{"gee_tasks": [...], ...}` (intermediate metadata)
- After Phase 3: contains the final `RasterResults` JSON (same as today)

### New Celery beat task: `poll_gee_tasks`

```python
# gefapi/tasks/gee_task_polling.py

"""CENTRALIZED GEE TASK POLLING

Replaces per-container polling threads with a single polling task.
Two triggers invoke poll_gee_tasks:

1. Celery beat (every 2 min): safety-net for long-running tasks. The task
   short-circuits immediately (no EE call, no Redis lock) when no GEE
   executions are active in the database, so idle beats are effectively free.
2. Rapid burst: when a new GEE job is submitted (Phase 1 completes),
   schedule_rapid_polling() queues a burst of polls with exponential
   backoff: 2s, 4s, 8s, 16s, 32s, 60s, 90s, 120s, 150s, 180s, 210s, 240s.
   This catches quickly-returned results without waiting for the next
   beat cycle.

Since poll_gee_tasks always batch-queries ALL pending GEE tasks in one
ee.data.getTaskList() call, rapid polling triggered by one submission
benefits all currently running executions.

A Redis lock prevents overlapping polls from stacking up when multiple
submissions trigger rapid bursts concurrently.
"""

import datetime
import logging

import ee
import rollbar
from redis import Redis

from gefapi import celery, db
from gefapi.config import SETTINGS
from gefapi.models import Execution, ExecutionLog

logger = logging.getLogger(__name__)

# Rapid-poll schedule (seconds after submission)
# Exponential backoff: 2, 4, 8, 16, 32, then linear 60-240
RAPID_POLL_SCHEDULE = [2, 4, 8, 16, 32, 60, 90, 120, 150, 180, 210, 240]


def _init_ee_if_needed():
    """Initialize Earth Engine for the worker process (once)."""
    try:
        ee.data.getTaskList()  # Test if already initialized
    except Exception:
        ee.Initialize()


def _get_redis():
    """Get Redis connection from configured URL."""
    return Redis.from_url(SETTINGS.get("REDIS_URL", "redis://redis:6379/0"))


def schedule_rapid_polling():
    """Schedule a burst of rapid polls after a new GEE job submission.

    Called by the API when Phase 1 completes. Queues poll_gee_tasks
    at each interval in RAPID_POLL_SCHEDULE using Celery countdown.

    Uses a Redis key to avoid stacking when multiple submissions happen
    close together — if a rapid-poll burst is already scheduled (key exists
    with TTL > 0), skip scheduling a new burst. The existing burst will
    pick up the new execution's tasks on its next poll.
    """
    r = _get_redis()
    lock_key = "gee_rapid_poll_active"

    # Only schedule if no burst is currently active.
    # The key auto-expires after the last scheduled poll (max of schedule + buffer).
    if not r.set(lock_key, "1", nx=True, ex=RAPID_POLL_SCHEDULE[-1] + 30):
        logger.debug("Rapid-poll burst already active, skipping")
        return

    logger.info(
        f"Scheduling rapid-poll burst: {len(RAPID_POLL_SCHEDULE)} polls "
        f"over {RAPID_POLL_SCHEDULE[-1]}s"
    )
    for delay in RAPID_POLL_SCHEDULE:
        poll_gee_tasks.apply_async(countdown=delay)


@celery.task(bind=True)
def poll_gee_tasks(self):
    """Poll all active GEE executions for task completion.

    Triggered by Celery beat (every 2 min) and by rapid-burst scheduling
    after new submissions. Short-circuits immediately when no GEE executions
    are active — the beat becomes a no-op in the common idle case.

    When work exists, acquires a Redis lock to prevent overlapping polls and
    makes a single ee.data.getTaskList() call covering ALL GEE tasks for the
    service account.
    """
    from gefapi import app

    # ── Quick check: anything to poll? ──────────────────────────────
    # This runs BEFORE EE init or Redis lock, so idle beats are cheap.
    with app.app_context():
        active_count = (
            db.session.query(Execution.id)
            .filter(Execution.status == "RUNNING")
            .filter(Execution.results["gee_tasks"].isnot(None))
            .filter(Execution.results["gee_finalize_dispatched"].astext != "true")
            .filter(
                Execution.start_date
                >= datetime.datetime.utcnow() - datetime.timedelta(days=3)
            )
            .limit(1)
            .count()
        )
    if active_count == 0:
        return {"checked": 0, "completed": 0, "failed": 0}

    # ── Real work: acquire lock, init EE, poll ─────────────────────
    r = _get_redis()
    poll_lock = r.lock("gee_poll_running", timeout=120, blocking=False)
    if not poll_lock.acquire(blocking=False):
        logger.debug("Another poll_gee_tasks is running, skipping")
        return {"skipped": True}

    try:
        with app.app_context():
            _init_ee_if_needed()

            gee_executions = (
                db.session.query(Execution)
                .filter(Execution.status == "RUNNING")
                .filter(Execution.results["gee_tasks"].isnot(None))
                .filter(Execution.results["gee_finalize_dispatched"].astext != "true")
                .filter(
                    Execution.start_date
                    >= datetime.datetime.utcnow() - datetime.timedelta(days=3)
                )
                .order_by(Execution.start_date.desc())
                .limit(200)
                .all()
            )

            # Collect all GEE task IDs across all executions for batch query
            all_task_ids = set()
            for execution in gee_executions:
                results = execution.results or {}
                for task_meta in results.get("gee_tasks", []):
                    task_id = task_meta.get("task_id")
                    if task_id:
                        all_task_ids.add(task_id)

            # Batch query GEE task statuses (single API call)
            task_statuses = {}
            if all_task_ids:
                try:
                    task_list = ee.data.getTaskList()
                    for t in task_list:
                        if t.get("id") in all_task_ids:
                            task_statuses[t["id"]] = t.get("state", "UNKNOWN")
                except Exception as e:
                    logger.error(f"Failed to query GEE task list: {e}")
                    rollbar.report_exc_info()
                    return {"checked": len(gee_executions), "completed": 0, "failed": 0}

            completed = 0
            failed = 0

            for execution in gee_executions:
                try:
                    results = execution.results or {}
                    gee_tasks = results.get("gee_tasks", [])

                    if not gee_tasks:
                        continue

                    # Check status of all tasks for this execution
                    statuses = []
                    for task_meta in gee_tasks:
                        task_id = task_meta.get("task_id")
                        status = task_statuses.get(task_id, "UNKNOWN")
                        statuses.append(status)

                    if any(s == "FAILED" for s in statuses):
                        # At least one task failed — execution fails
                        execution.status = "FAILED"
                        execution.end_date = datetime.datetime.utcnow()
                        execution.progress = 100
                        failed_ids = [
                            gee_tasks[i]["task_id"]
                            for i, s in enumerate(statuses)
                            if s == "FAILED"
                        ]
                        db.session.add(
                            ExecutionLog(
                                text=f"GEE task(s) failed: {', '.join(failed_ids)}",
                                level="ERROR",
                                execution_id=execution.id,
                            )
                        )
                        db.session.add(execution)
                        db.session.commit()
                        failed += 1
                        logger.warning(
                            f"Execution {execution.id}: GEE tasks failed: {failed_ids}"
                        )

                    elif all(s == "COMPLETED" for s in statuses):
                        # All tasks completed — dispatch Phase 3 container
                        try:
                            _dispatch_finalize(execution)
                            # Mark as dispatched so we don't re-dispatch
                            results["gee_finalize_dispatched"] = True
                            execution.results = results
                            db.session.add(execution)
                            db.session.commit()
                            completed += 1
                            logger.info(
                                f"Execution {execution.id}: all GEE tasks completed, "
                                f"dispatched Phase 3 finalize container"
                            )
                        except Exception as e:
                            execution.status = "FAILED"
                            execution.end_date = datetime.datetime.utcnow()
                            execution.progress = 100
                            db.session.add(
                                ExecutionLog(
                                    text=f"Failed to dispatch finalize container: {str(e)[:500]}",
                                    level="ERROR",
                                    execution_id=execution.id,
                                )
                            )
                            db.session.add(execution)
                            db.session.commit()
                            failed += 1
                            logger.error(
                                f"Execution {execution.id}: finalize dispatch failed: {e}"
                            )
                            rollbar.report_exc_info()

                    else:
                        # Tasks still running — update progress
                        completed_count = sum(1 for s in statuses if s == "COMPLETED")
                        pct = int(completed_count / len(statuses) * 100)
                        if pct != execution.progress:
                            execution.progress = pct
                            db.session.add(execution)
                            db.session.commit()

                except Exception as e:
                    logger.error(f"Error polling execution {execution.id}: {e}")
                    rollbar.report_exc_info()

            return {
                "checked": len(gee_executions),
                "completed": completed,
                "failed": failed,
            }
    finally:
        try:
            poll_lock.release()
        except Exception:
            pass


def _dispatch_finalize(execution):
    """Dispatch a Phase 3 finalize container for a completed GEE execution.

    Runs the same script image as Phase 1, but with EXECUTION_MODE=gee_finalize.
    The container calls the script's finalize() function to collect GCS results
    and run any post-processing.
    """
    import json
    from gefapi.tasks.docker_run import docker_run  # or k8s_run

    script = execution.script
    gee_metadata = execution.results  # Contains gee_tasks, task_name, bucket, etc.

    environment = {
        "EXECUTION_MODE": "gee_finalize",
        "GEE_TASK_METADATA": json.dumps(gee_metadata),
        "EXECUTION_ID": str(execution.id),
    }

    # Re-use the same dispatch path as Phase 1 (docker_run or k8s_run)
    # The container is short-lived (~10s) — lightweight enough for the
    # always-on node unless it is saturated.
    docker_run.delay(
        str(execution.id),
        script.slug,
        environment,
        execution.params,
    )
```

### Celery beat schedule

```python
# Add to CELERYBEAT_SCHEDULE:
"poll-gee-tasks": {
    "task": "gefapi.tasks.gee_task_polling.poll_gee_tasks",
    "schedule": crontab(minute="*/2"),  # Safety-net for long-running tasks
},
# The task short-circuits (no EE call) when nothing is active, so idle
# beats are essentially free. Rapid-burst polls are scheduled separately
# via schedule_rapid_polling() when Phase 1 completes.
```

### Polling behavior

```
Time after GEE submission:
  +2s   poll  ←  rapid burst (catches very fast tasks like download-data)
  +4s   poll
  +8s   poll
  +16s  poll
  +32s  poll
  +60s  poll
  +90s  poll      ← most single-export tasks finish within 1-2 min
  +120s poll  ←  overlaps with first beat cycle
  +150s poll
  +180s poll
  +210s poll
  +240s poll  ←  last rapid poll
  ... then every 2 min via Celery beat (safety-net for long tasks)
```

The beat always runs every 2 minutes, but `poll_gee_tasks` short-circuits
with a single cheap DB query when no GEE executions are active — no EE
initialization, no Redis lock, no `getTaskList()` call. So idle beats
cost only a lightweight `COUNT ... LIMIT 1` query.

When work IS present, each poll queries ALL GEE tasks in one
`ee.data.getTaskList()` call, so the rapid-burst triggered by one
submission benefits every currently running execution. The Redis lock
prevents overlapping polls from stacking up when multiple submissions
happen close together.

### Task routing

```python
# Route to the default worker (not the build worker)
"gefapi.tasks.gee_task_polling.poll_gee_tasks": {"queue": "default"},
```

The default worker needs GEE credentials (service account). This is already the
case for the current Docker Swarm setup where the same EC2 instance runs both
the Celery worker and execution containers.

---

## Phase 3: Finalize (Container Changes)

Phase 3 runs the **same script image** as Phase 1. The runner detects
`EXECUTION_MODE=gee_finalize` and calls `main.finalize()` instead of
`main.run()`. This keeps all result-processing logic in the script code.

### Why a container instead of Celery

| Concern | Celery approach | Container approach |
|---------|----------------|--------------------|
| Code ownership | Post-processing logic lives in API (gefapi) | Post-processing logic lives in script code |
| User changes | Requires API deployment for any change | Users modify scripts only — no API changes |
| Dependencies | API needs te_schemas, te_algorithms | Container already has all dependencies |
| Isolation | Runs in shared worker process | Runs in isolated container |

### Script `finalize()` function

Every GEE export script gets a `finalize(gee_metadata, params, logger)` function.
The default is a one-liner, but scripts with post-processing override it.

#### Simple scripts (8 of 11) — default collect_results

These scripts have a single `export()` call and return directly:
- land-cover, soil-organic-carbon, total-carbon, download-data,
  restoration-biomass, drought-vulnerability, sdg-15-3-1-indicator, landpks

```python
# e.g., land-cover/src/main.py


def finalize(gee_metadata, params, logger):
    """Phase 3: collect GCS results after GEE tasks complete."""
    from te_algorithms.gee.util import TEImageV2

    return TEImageV2.collect_results(gee_metadata)
```

#### productivity (FAO-WOCAT mode) — multi-export combine

In FAO-WOCAT mode, `run()` submits separate exports per geojson, then
deserializes + `combine()` + re-serializes. Phase 3 must replicate this:

```python
# productivity/src/main.py


def finalize(gee_metadata, params, logger):
    """Phase 3: collect and combine results.

    For FAO-WOCAT mode with multiple geojsons, each geojson produced a
    separate export. We need to deserialize each, combine them into a
    single RasterResults, and re-serialize.

    For Trends.Earth mode, there's a single global export — just collect.
    """
    from te_algorithms.gee.util import TEImageV2
    from te_schemas import results

    prod_mode = params.get("productivity", {}).get("mode")
    geojsons = json.loads(params.get("geojsons", "[]"))

    if prod_mode == ProductivityMode.FAO_WOCAT_5_CLASS_LPD.value and len(geojsons) > 1:
        # FAO-WOCAT: Phase 1 submitted separate exports per geojson,
        # each with execution_id suffixed by _1, _2, etc.
        # Group gee_tasks by their execution_id suffix and collect each group.
        groups = _group_tasks_by_geojson(gee_metadata)
        schema = results.RasterResults.Schema()
        final_output = schema.load(TEImageV2.collect_results(groups[0]))
        for group in groups[1:]:
            logger.debug("Combining FAO-WOCAT output")
            final_output.combine(schema.load(TEImageV2.collect_results(group)))
        return schema.dump(final_output)
    else:
        return TEImageV2.collect_results(gee_metadata)
```

#### sdg-15-3-1-sub-indicators (FAO-WOCAT mode) — same pattern

```python
# sdg-15-3-1-sub-indicators/src/main.py


def finalize(gee_metadata, params, logger):
    """Phase 3: collect and optionally combine results."""
    from te_algorithms.gee.util import TEImageV2
    from te_schemas import results

    prod_mode = params.get("productivity", {}).get("mode")

    if prod_mode == ProductivityMode.FAO_WOCAT_5_CLASS_LPD.value:
        groups = _group_tasks_by_geojson(gee_metadata)
        schema = results.RasterResults.Schema()
        final_output = schema.load(TEImageV2.collect_results(groups[0]))
        for group in groups[1:]:
            logger.debug("Combining FAO-WOCAT output")
            final_output.combine(schema.load(TEImageV2.collect_results(group)))
        return schema.dump(final_output)
    else:
        return TEImageV2.collect_results(gee_metadata)
```

#### urban-area — multi-geojson URI merge

```python
# urban-area/src/main.py


def finalize(gee_metadata, params, logger):
    """Phase 3: collect and merge results across geojsons.

    Urban area exports one result per geojson, then merges URI lists.
    """
    from te_algorithms.gee.util import TEImageV2
    from te_schemas import results

    geojsons = json.loads(params.get("geojsons", "[]"))

    if len(geojsons) <= 1:
        return TEImageV2.collect_results(gee_metadata)

    groups = _group_tasks_by_geojson(gee_metadata)
    schema = results.RasterResults.Schema()
    final_output = schema.load(TEImageV2.collect_results(groups[0]))

    for group in groups[1:]:
        this_out = schema.load(TEImageV2.collect_results(group))
        for datatype, raster in this_out.data.items():
            final_output.data[datatype].uri.extend(raster.uri)

    return schema.dump(final_output)
```

### Shared helper: `_group_tasks_by_geojson`

Scripts that submit multiple exports (one per geojson) need to group
the `gee_tasks` list back into per-geojson groups for `collect_results()`:

```python
# Could live in te_algorithms/gee/util.py or as a local helper


def _group_tasks_by_geojson(gee_metadata):
    """Group gee_tasks by execution_id suffix (e.g., EXECID_1, EXECID_2).

    During Phase 1, multi-geojson scripts append _{idx} to the execution_id
    for each geojson. Group tasks back by that suffix so each group can be
    passed to collect_results() independently.
    """
    from collections import defaultdict
    import re

    tasks_by_group = defaultdict(list)
    for task in gee_metadata["gee_tasks"]:
        # Extract the geojson index from prefix: "EXECID_N_taskname_datatype_M"
        # The _N suffix was added by the script in export_async
        prefix = task["prefix"]
        # Match execution_id pattern — group index is embedded in the prefix
        match = re.match(r"(.+?)_(\d+)_(.+)", prefix)
        if match:
            group_key = match.group(2)  # The geojson index
        else:
            group_key = "0"
        tasks_by_group[group_key].append(task)

    # Return list of gee_metadata dicts, one per group
    groups = []
    for key in sorted(tasks_by_group.keys()):
        groups.append(
            {
                "gee_tasks": tasks_by_group[key],
                "task_name": gee_metadata.get("task_name"),
                "bucket": gee_metadata.get("bucket"),
                "filetype": gee_metadata.get("filetype"),
            }
        )
    return groups
```

### Container resource requirements

Phase 3 containers are lightweight:
- **CPU**: Minimal (HTTP calls to GCS, JSON manipulation)
- **Memory**: ~50-100 MB (deserialize + combine schemas)
- **Duration**: ~10 seconds
- **Scheduling**: Will normally land on the always-on platform node unless
  it is saturated, in which case Karpenter spins up a burst node.
  Since Phase 3 completes in seconds, even a cold-start burst node is
  acceptable.

---

## Execution Dispatch Changes

### `gefapi/services/execution_service.py`

Update `_dispatch_execution()` to pass `EXECUTION_MODE` to the container:

```python
def _dispatch_execution(execution_id, script_slug, environment, params, is_batch):
    """Dispatch execution based on orchestrator and execution mode."""
    orchestrator = SETTINGS.get("ORCHESTRATOR", "docker")

    # Look up script to determine execution mode
    script = Script.query.filter_by(slug=script_slug).first()
    execution_mode = getattr(script, "execution_mode", "standard") or "standard"

    # Pass execution mode to the container
    environment["EXECUTION_MODE"] = execution_mode

    if orchestrator == "docker":
        if is_batch:
            batch_run.delay(execution_id, script_slug, environment, params)
        else:
            docker_run.delay(execution_id, script_slug, environment, params)
    elif orchestrator == "k8s":
        if is_batch:
            batch_run.delay(execution_id, script_slug, environment, params)
        else:
            k8s_run.delay(execution_id, script_slug, environment, params)
```

No other dispatch changes needed. The container receives `EXECUTION_MODE` as an
environment variable and decides whether to call `export_async()` or `export()`.

---

## Database Migration

### New column on Script

```python
# Alembic migration
def upgrade():
    op.add_column(
        "script",
        sa.Column(
            "execution_mode", sa.String(40), nullable=False, server_default="standard"
        ),
    )


def downgrade():
    op.drop_column("script", "execution_mode")
```

### Update existing GEE export scripts

After migration, update the 11 GEE export scripts to `execution_mode="gee_async"`:

```sql
UPDATE script SET execution_mode = 'gee_async'
WHERE slug IN (
    'productivity',
    'land-cover',
    'sdg-15-3-1-indicator',
    'sdg-15-3-1-sub-indicators',
    'soil-organic-carbon',
    'total-carbon',
    'restoration-biomass',
    'download-data',
    'urban-area',
    'drought-vulnerability',
    'landpks'
);
```

Scripts that don't export (time-series, sdg-15-3-1-stats,
unccd-sdg-15-3-1-error-recode) stay `execution_mode="standard"` because
they complete quickly in-container without long-running GEE tasks.

---

## Rollout Strategy

This can be deployed **incrementally** with zero risk:

### Step 1: Deploy API changes (backward-compatible)

1. Add `execution_mode` column (default `"standard"` — no behavioral change)
2. Deploy `poll_gee_tasks` Celery beat task (does nothing — no gee_async executions exist)

At this point, everything works exactly as before.

### Step 2: Deploy container changes (backward-compatible)

1. Add `export_async()` and `collect_results()` to `te_algorithms/gee/util.py`
2. Update `gefcore/runner.py` to handle `EXECUTION_MODE` (`gee_async` and `gee_finalize`)
3. Update script `main.py` files with if/else in `run()` and add `finalize()`

At this point, all scripts still use `export()` because `EXECUTION_MODE` defaults
to `"standard"`.

### Step 3: Enable per-script (controlled rollout)

1. Pick a low-risk script (e.g., `download-data` — simplest export, default `finalize()`)
2. Set `execution_mode = 'gee_async'` in the database
3. Run a test execution and verify:
   - Phase 1 container exits in ~30 seconds
   - Execution status stays `RUNNING`, results contain `gee_tasks`
   - `poll_gee_tasks` picks it up and polls GEE
   - After GEE completes, poller dispatches Phase 3 container
   - Phase 3 calls `finalize()`, collects GCS results
   - Execution transitions to `FINISHED` with correct `RasterResults` JSON
4. If anything fails, set back to `"standard"` — instant rollback
5. Enable remaining simple scripts, then test the three custom-finalize
   scripts (productivity, sub-indicators, urban-area) individually

### Step 4: Rollback plan

Set `execution_mode = 'standard'` on any script to revert to the old behavior
instantly. No code deployment needed.

---

## Status Flow

No new statuses are introduced. The flow is:

```
PENDING → RUNNING                 → FINISHED
           ↑                         ↑
     Phase 1 container starts   Phase 3 container calls send_result()
     (status set to RUNNING      
      by runner.py as today)    If Phase 1 or 3 fails → FAILED
                                If GEE task fails     → FAILED (set by poller)
```

During the gap between Phase 1 exit and Phase 3 start, the execution remains
`RUNNING`. The plugin shows it as in-progress (blue, buttons disabled). From
the user's perspective, nothing has changed.

The poller distinguishes "real" running containers from GEE-polling executions
by checking `execution.results->'gee_tasks'`. This is an internal API concern
that no downstream consumer needs to know about.

---

## Relationship to K8s Migration

This plan is a **prerequisite** for safe Spot node usage in the K8s architecture:

| Without this plan | With this plan |
|---|---|
| Execution containers run for hours | Containers exit in ~40 seconds (Phase 1 + Phase 3) |
| Spot reclamation = lost execution | Spot reclamation = no impact (GEE tasks tracked in DB) |
| `backoff_limit=0` is risky | `backoff_limit=0` is fine (container's job is done quickly) |
| Warm pool + On-Demand needed for all | Only standard-mode scripts need warm pool; gee_async scripts are so fast they tolerate cold starts |
| Node cost scales with GEE processing time | Node cost is fixed ~$0.0006 per execution regardless of GEE runtime |

Once this is implemented, the K8s migration plan can:
- Use Spot nodes for all `gee_async` executions without risk
- Reserve the On-Demand warm pool for `standard`-mode scripts only
- Scale execution nodes to zero between submissions (containers are short-lived)

The K8s migration plan document ([k8s-migration-plan.md](k8s-migration-plan.md))
references this plan as a prerequisite for Spot resilience.

---

## Implementation Checklist

| Step | Repo | Change | Risk |
|------|------|--------|------|
| 1a | trends.earth-API | Add `execution_mode` column + migration | None — defaults to `"standard"` |
| 1b | trends.earth-API | Create `gefapi/tasks/gee_task_polling.py` + beat schedule | None — no gee_async executions to poll |
| 1c | trends.earth-API | Update `_dispatch_execution()` to pass `EXECUTION_MODE` env var | None — defaults to `"standard"` |
| 2a | trends.earth-algorithms | Add `TEImageV2.export_async()` + `collect_results()` + `_get_gcs_uris()` | None — not called until script opts in |
| 2b | trends.earth-Environment | Update `runner.py` to handle `gee_async` and `gee_finalize` modes | None — env var not set until script opts in |
| 2c | trends.earth | Update each GEE export script's `run()` with if/else on `EXECUTION_MODE` | None — defaults to old path |
| 2d | trends.earth | Add `finalize()` function to each GEE export script | None — not called until `gee_async` enabled |
| 3 | trends.earth-API (DB) | `UPDATE script SET execution_mode='gee_async'` per script | Reversible — set back to `"standard"` |

**No downstream changes needed**: No changes to `te_schemas`, QGIS plugin, or admin UI.

**Total files changed**: ~17 (1 migration, 1 model, 1 service, 1 new task, 1 celery config, 1 util.py, 1 runner.py, 11 script main.py files)

---

## Appendix A: Script Classification Reference

| Script | Slug | GEE Export Tasks | Execution Mode | `finalize()` |
|--------|------|-----------------|----------------|-------------|
| Productivity | `productivity` | Yes (multi in FAO-WOCAT) | `gee_async` | Custom (combine per geojson in FAO-WOCAT mode) |
| Land Cover | `land-cover` | Yes | `gee_async` | Default `collect_results` |
| SDG 15.3.1 Indicator | `sdg-15-3-1-indicator` | Yes | `gee_async` | Default `collect_results` |
| SDG 15.3.1 Sub-indicators | `sdg-15-3-1-sub-indicators` | Yes (multi in FAO-WOCAT) | `gee_async` | Custom (combine per geojson in FAO-WOCAT mode) |
| Soil Organic Carbon | `soil-organic-carbon` | Yes | `gee_async` | Default `collect_results` |
| Total Carbon | `total-carbon` | Yes | `gee_async` | Default `collect_results` |
| Restoration Biomass | `restoration-biomass` | Yes | `gee_async` | Default `collect_results` |
| Download Data | `download-data` | Yes | `gee_async` | Default `collect_results` |
| Urban Area | `urban-area` | Yes (multi per geojson) | `gee_async` | Custom (merge URI lists across geojsons) |
| Drought Vulnerability | `drought-vulnerability` | Yes | `gee_async` | Default `collect_results` |
| LandPKS | `landpks` | Yes (partial) | `gee_async` | Default `collect_results` |
| Time Series | `time-series` | No (reduceRegion) | `standard` | N/A |
| SDG 15.3.1 Stats | `sdg-15-3-1-stats` | No (GDAL) | `standard` | N/A |
| UNCCD Error Recode | `unccd-sdg-15-3-1-error-recode` | No (S3/GDAL) | `standard` | N/A |

## Appendix B: Post-Processing Patterns

Three scripts require custom `finalize()` implementations because they submit
multiple GEE exports and merge results after completion:

### Pattern 1: `RasterResults.combine()` (productivity, sub-indicators in FAO-WOCAT mode)

**Current behavior** (in `run()`):
```python
# Submit separate export per geojson with ThreadPoolExecutor
res = []
with ThreadPoolExecutor(max_workers=4) as executor:
    for idx, geojson in enumerate(geojsons, start=1):
        res.append(executor.submit(
            lpd_img.export, geojsons=[geojson], ...,
            execution_id=f"{EXECUTION_ID}_{idx}"
        ))

# After all exports complete: deserialize, combine, re-serialize
schema = results.RasterResults.Schema()
final_output = schema.load(res[0].result())
for n, this_res in enumerate(as_completed(res[1:]), start=1):
    final_output.combine(schema.load(this_res.result()))
return schema.dump(final_output)
```

`combine()` is a metadata-only operation in `te_schemas/results.py` — it merges
URI lists and converts `Raster` → `TiledRaster`. No raster data is downloaded
or processed. This is why it runs in seconds in Phase 3.

### Pattern 2: URI list extension (urban-area)

**Current behavior** (in `run()`):
```python
# Export one result per geojson
outs = []
for geojson in geojsons:
    outs.append(this_out.export([geojson], "urban", ...))

# After all exports: deserialize, extend URI lists, re-serialize
schema = results.RasterResults.Schema()
final_output = schema.load(outs[0])
for o in outs[1:]:
    this_out = schema.load(o)
    for datatype, raster in this_out.data.items():
        final_output.data[datatype].uri.extend(raster.uri)
return schema.dump(final_output)
```

Same principle — purely metadata manipulation, completes instantly.
