"""openEO dispatch service for remote script executions.

This service provides a Celery task to submit script executions to an
**openEO backend** rather than to a local Docker daemon or AWS Batch.
It is the openEO counterpart of ``docker_service.docker_run`` and
``batch_service.batch_run``.

Design
------
The service is intentionally script-agnostic.  Any ``Script`` whose
``compute_type`` is ``"openeo"`` will be dispatched here.  The openEO
backend URL is resolved in this order:

1. ``environment["OPENEO_BACKEND_URL"]`` – injected at dispatch time.
2. ``SETTINGS["OPENEO_DEFAULT_BACKEND_URL"]`` – system-wide fallback.

On successful submission the openEO job ID is stored in
``execution.results["openeo_job_id"]`` and execution status is set to
``"READY"``.  The :mod:`gefapi.tasks.openeo_monitoring` periodic task
polls the backend and drives all subsequent status transitions.
"""

import logging

from celery import Task
import rollbar

from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import Execution, ExecutionLog

logger = logging.getLogger(__name__)


class OpenEOServiceTask(Task):
    """Base task class for openEO dispatch."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("openEO service task failed: %s", exc)
        rollbar.report_exc_info()


# Import celery after other imports to avoid circular dependency –
# follows the same pattern used throughout the project.
from gefapi import celery  # noqa: E402


def _resolve_backend_url(environment):
    """Return the openEO backend URL from environment or settings fallback.

    Validates that the URL uses https:// to prevent SSRF.
    """
    from urllib.parse import urlparse

    url = (environment or {}).get("OPENEO_BACKEND_URL") or SETTINGS.get(
        "OPENEO_DEFAULT_BACKEND_URL"
    )
    if not url:
        raise ValueError(
            "No openEO backend URL configured. Set OPENEO_BACKEND_URL in the "
            "execution environment or OPENEO_DEFAULT_BACKEND_URL in settings."
        )
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"openEO backend URL must use https://, got: '{url}'. "
            "Non-https schemes are not permitted."
        )
    if not parsed.netloc:
        raise ValueError(f"openEO backend URL has no host: '{url}'.")
    return url


def _connect_openeo(backend_url, environment):
    """Return an authenticated openEO Connection.

    Authentication priority:
    1. ``OPENEO_CREDENTIALS`` in environment (user-specific credentials JSON).
    2. Anonymous connection (for backends that allow it).
    """
    import json

    try:
        import openeo  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "The 'openeo' package is required for openEO execution support. "
            "Install it with: pip install openeo"
        ) from exc

    connection = openeo.connect(backend_url)

    credentials_json = (environment or {}).get("OPENEO_CREDENTIALS")
    if credentials_json:
        try:
            creds = json.loads(credentials_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Invalid OPENEO_CREDENTIALS JSON: {exc}") from exc

        # Accept both the canonical storage type name ("oidc_refresh_token") and
        # the shorter alias ("oidc") so the service is resilient to either form.
        cred_type = creds.get("type", "oidc_refresh_token")
        if cred_type == "basic":
            connection.authenticate_basic(
                username=creds["username"],
                password=creds["password"],
            )
        elif cred_type in ("oidc_refresh_token", "oidc"):
            # Refresh token flow (non-interactive)
            provider = creds.get("provider_id")
            client_id = creds.get("client_id")
            client_secret = creds.get("client_secret")
            refresh_token = creds.get("refresh_token")
            if refresh_token and client_id:
                connection.authenticate_oidc_refresh_token(
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=refresh_token,
                    provider_id=provider,
                )
            else:
                logger.warning(
                    "[OPENEO]: OIDC credentials incomplete – proceeding unauthenticated"
                )
        else:
            logger.warning(
                "[OPENEO]: Unknown credential type '%s' – proceeding unauthenticated",
                cred_type,
            )
    else:
        logger.info("[OPENEO]: No user credentials provided – connecting anonymously")

    return connection


def _build_process_graph(connection, script_slug, environment, params):
    """Build and return an openEO ``BatchJob`` for *script_slug*.

    Currently routes to per-slug process graph builders.  Unknown slugs
    raise ``NotImplementedError`` so that misconfigured scripts fail fast
    at submission time rather than silently.
    """
    if script_slug == "soil-organic-carbon":
        return _build_soc_process_graph(connection, environment, params)

    raise NotImplementedError(
        f"No openEO process graph defined for script slug '{script_slug}'. "
        "Add a builder in gefapi/services/openeo_service.py."
    )


def _build_soc_process_graph(connection, environment, params):
    """Build the SOC openEO BatchJob.

    Delegates to the algorithm module in te_algorithms so that the
    process graph logic lives alongside other algorithm code.
    """
    import json

    from te_schemas.land_cover import LCLegendNesting

    try:
        from te_algorithms.openeo.soc import soc as soc_openeo
    except ImportError as exc:
        raise ImportError(
            "te_algorithms.openeo.soc is required for SOC openEO execution. "
            "Ensure te_algorithms is installed and the openeo subpackage exists."
        ) from exc

    year_initial = params.get("year_initial")
    year_final = params.get("year_final")
    fl = params.get("fl")
    annual_lc = params.get("download_annual_lc")
    annual_soc = params.get("download_annual_soc")
    esa_to_custom_nesting = LCLegendNesting.Schema().load(
        params.get("legend_nesting_esa_to_custom")
    )
    ipcc_nesting = LCLegendNesting.Schema().load(
        params.get("legend_nesting_custom_to_ipcc")
    )
    geojsons = json.loads(params.get("geojsons"))
    execution_id = environment.get("EXECUTION_ID") or params.get("EXECUTION_ID")

    return soc_openeo(
        year_initial=year_initial,
        year_final=year_final,
        fl=fl,
        esa_to_custom_nesting=esa_to_custom_nesting,
        ipcc_nesting=ipcc_nesting,
        annual_lc=annual_lc,
        annual_soc=annual_soc,
        logger=logger,
        connection=connection,
        geojsons=geojsons,
        execution_id=execution_id,
    )


@celery.task(base=OpenEOServiceTask, bind=True)
def openeo_run(self, execution_id, script_slug, environment, params):
    """Submit an execution to an openEO backend.

    Steps:
    1. Resolve backend URL from environment or settings.
    2. Connect and authenticate.
    3. Build the process graph for *script_slug*.
    4. Submit the job; store the returned job ID in
       ``execution.results["openeo_job_id"]``.
    5. Set ``execution.status = "READY"``.

    On failure, set ``execution.status = "FAILED"`` and log the error.
    """
    logger.info("[OPENEO]: Starting dispatch for execution %s", execution_id)

    from gefapi import app

    with app.app_context():
        execution = Execution.query.get(execution_id)
        if not execution:
            logger.error("[OPENEO]: Execution %s not found", execution_id)
            return

        try:
            backend_url = _resolve_backend_url(environment)
            logger.info("[OPENEO]: Connecting to backend %s", backend_url)

            connection = _connect_openeo(backend_url, environment)

            logger.info("[OPENEO]: Building process graph for script '%s'", script_slug)
            job = _build_process_graph(connection, script_slug, environment, params)

            logger.info("[OPENEO]: Submitting job for execution %s", execution_id)
            job.start_job()

            job_id = job.job_id
            logger.info(
                "[OPENEO]: Job %s submitted for execution %s", job_id, execution_id
            )

            # Store job ID so the monitor can poll it
            execution.results = {
                **(execution.results or {}),
                "openeo_job_id": job_id,
                "openeo_backend_url": backend_url,
            }
            execution.status = "READY"
            db.session.commit()

            # Add a log entry for the submission
            log_entry = ExecutionLog(
                text=f"openEO job submitted: {job_id} on {backend_url}",
                level="INFO",
                register_date=__import__("datetime").datetime.utcnow(),
                execution_id=execution_id,
            )
            db.session.add(log_entry)
            db.session.commit()

            logger.info(
                "[OPENEO]: Execution %s is now READY with job %s",
                execution_id,
                job_id,
            )

        except Exception as exc:
            logger.error(
                "[OPENEO]: Failed to submit execution %s: %s", execution_id, exc
            )
            rollbar.report_exc_info()

            try:
                execution = Execution.query.get(execution_id)
                if execution:
                    execution.status = "FAILED"
                    execution.results = {
                        **(execution.results or {}),
                        "openeo_error": str(exc),
                    }
                    db.session.commit()
            except Exception as inner_exc:
                logger.error(
                    "[OPENEO]: Could not mark execution %s as FAILED: %s",
                    execution_id,
                    inner_exc,
                )
