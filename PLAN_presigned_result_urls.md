# Plan: Secure GEE Output Downloads with Pre-signed URLs

## Context / Problem

`OUTPUT_S3_BUCKET` (stores openEO/GEE GeoTIFF outputs) is currently publicly accessible.
Anyone who knows or can guess an execution ID can construct the S3 URL and download any user's
results without authentication:

```
s3://OUTPUT_S3_BUCKET/{OUTPUT_S3_PREFIX}/{execution_id}/some_output.tif
```

This plan secures result downloads by:
1. Adding an authenticated API endpoint that generates ephemeral pre-signed S3 URLs.
2. Updating the QGIS plugin to fetch those URLs before downloading.
3. Making `OUTPUT_S3_BUCKET` private (infrastructure step, done last).

**Out of scope:** `PARAMS_S3_BUCKET` (already private; JSON results go there for batch jobs,
not accessed directly by clients), `trends.earth-api-ui` (shows metadata only, no file
downloads), `SCRIPTS_S3_BUCKET`.

---

## Current Architecture

### Backend (trends.earth-API)

- `gefapi/models/execution.py` — `Execution.results` is a `JSONB` column that stores arbitrary
  JSON, including `s3://` or `https://` URIs pointing to output files.
- `gefapi/config/base.py` — `OUTPUT_S3_BUCKET` and `OUTPUT_S3_PREFIX` (default `"outputs"`)
  config keys at **top level** (not inside `"environment"` sub-dict).
- `gefapi/s3.py` — Existing helpers: `push_script_to_s3`, `push_params_to_s3`,
  `delete_script_from_s3`, `get_script_from_s3`, `_sanitize_object_basename`. All use
  `boto3.client("s3")` with no ACL = private by default.
- `gefapi/services/execution_service.py:809` — `get_execution(execution_id, user)`:
  - `user == "fromservice"` or admin → fetches any execution.
  - Regular user → filters by `user_id`, raises `ExecutionNotFound` if not found/unauthorized.
  - This is the canonical ownership check; reuse it everywhere.
- `gefapi/routes/api/v1/executions.py:891` — Existing `GET /execution/<id>/download-results`
  uses `@jwt_required()` + `@require_scope("execution:read")` + `get_execution(id, current_user)`.
  **Mirror this exact pattern** for the new endpoint.
- `gefapi/errors.py` — `ExecutionNotFound` exception with `.message` attribute.
- `tests/conftest.py` — Fixtures: `app`, `client`, `regular_user`, `sample_script`,
  `sample_execution`, `auth_headers_user`, `auth_headers_admin`. AWS env vars already set to
  mock values (`AWS_ACCESS_KEY_ID="test_access_key"`, etc.).
- `tests/test_execution_logs_endpoints.py` — Best example to follow for new test class
  (uses `@pytest.mark.usefixtures("client")`, `execution_id` fixture, `patch()`).

### openEO Execution → S3 path

Output files land at:

```
s3://{OUTPUT_S3_BUCKET}/{OUTPUT_S3_PREFIX}/{execution_id}/{filename}.tif
```

(See `te_algorithms/openeo/util.py::build_s3_output_path`.)

### QGIS Plugin (trends.earth)

- `LDMP/jobs/manager.py:3580` — `get_remote_job_with_results(job_id)` fetches
  `GET /api/v1/execution/{id}?exclude=params` with JWT.
- `LDMP/jobs/manager.py:2414` — `_download_cloud_results(job, result)` handles raster
  downloads; calls `_download_result(url, output_path, expected_etag)`.
- `LDMP/jobs/manager.py:3426` — `_download_result(url, output_path, expected_etag)` uses
  `ldmp_download.Download(url, str(output_path))`. Works with any HTTP(S) URL.
- `LDMP/jobs/manager.py` uses `self.api_client.call_api(endpoint, method, use_token=True)` for
  authenticated API calls.

---

## Implementation Steps

### Phase 1 — Backend API (trends.earth-API)

#### Step 1: `gefapi/config/base.py` — Add expiry setting

Add the following key to the top-level `SETTINGS` dict (after `OUTPUT_S3_PREFIX`):

```python
"PRESIGNED_URL_EXPIRY_SECONDS": int(
    os.getenv("PRESIGNED_URL_EXPIRY_SECONDS", 3600)
),
```

#### Step 2: `gefapi/s3.py` — Add two new functions

Append after `push_params_to_s3`:

```python
def list_execution_result_keys(
    bucket: str, prefix: str, execution_id: str
) -> list[str]:
    """Return all S3 object keys under {prefix}/{execution_id}/.

    Args:
        bucket: S3 bucket name.
        prefix: Top-level prefix (e.g. "outputs").
        execution_id: UUID string of the execution.

    Returns:
        List of S3 object keys (may be empty if no objects exist or bucket
        is not configured).
    """
    s3_client = boto3.client("s3")
    folder_prefix = f"{prefix}/{execution_id}/"
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=folder_prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
    except botocore.exceptions.ClientError as e:
        logger.error("[S3]: Failed to list result keys for %s: %s", execution_id, e)
        rollbar.report_exc_info()
    return keys


def generate_presigned_url(bucket: str, key: str, expiry: int) -> str:
    """Generate a pre-signed GET URL for an S3 object.

    Args:
        bucket: S3 bucket name.
        key: Full S3 object key.
        expiry: URL lifetime in seconds.

    Returns:
        Pre-signed HTTPS URL string.

    Raises:
        botocore.exceptions.ClientError: If URL generation fails.
    """
    s3_client = boto3.client("s3")
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry,
    )
```

#### Step 3: `gefapi/services/execution_service.py` — Add `get_result_presigned_urls`

Add as a new `@staticmethod` inside `ExecutionService`, after `get_execution`:

```python
@staticmethod
def get_result_presigned_urls(execution_id, user):
    """Generate pre-signed S3 URLs for all output files of an execution.

    Performs an ownership check via get_execution() — raises ExecutionNotFound
    if the execution does not exist or the user is not permitted to access it.

    Args:
        execution_id (str): UUID of the execution.
        user (User): Requesting user (from JWT).

    Returns:
        list[dict]: Each dict has keys ``filename`` (str), ``url`` (str),
            and ``expires_at`` (ISO-8601 UTC string).  Empty list if
            OUTPUT_S3_BUCKET is not configured or no files are found.

    Raises:
        ExecutionNotFound: If execution does not exist or user lacks access.
    """
    # Ownership check (raises ExecutionNotFound on failure)
    ExecutionService.get_execution(execution_id, user)

    bucket = SETTINGS.get("OUTPUT_S3_BUCKET")
    if not bucket:
        return []

    prefix = SETTINGS.get("OUTPUT_S3_PREFIX", "outputs")
    expiry = SETTINGS.get("PRESIGNED_URL_EXPIRY_SECONDS", 3600)

    from datetime import datetime, timezone

    from gefapi.s3 import generate_presigned_url, list_execution_result_keys

    keys = list_execution_result_keys(bucket, prefix, str(execution_id))
    result = []
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expiry)).isoformat()

    for key in keys:
        filename = key.rsplit("/", 1)[-1]
        try:
            url = generate_presigned_url(bucket, key, expiry)
        except Exception as exc:
            logger.error(
                "[SERVICE]: Failed to generate presigned URL for %s: %s", key, exc
            )
            rollbar.report_exc_info()
            continue
        result.append({"filename": filename, "url": url, "expires_at": expires_at})

    return result
```

> **Note:** `timedelta` is already imported at the top of `execution_service.py` via
> `from datetime import timedelta`. Confirm before adding a duplicate import.

#### Step 4: `gefapi/routes/api/v1/executions.py` — Add `/result-urls` endpoint

Add immediately after the `get_download_results` function (after line ~940):

```python
@endpoints.route(
    "/execution/<execution>/result-urls", strict_slashes=False, methods=["GET"]
)
@jwt_required()
@require_scope("execution:read")
def get_execution_result_urls(execution):
    """Generate pre-signed S3 URLs for all output files of an execution.

    **Authentication**: JWT token required
    **Access**: Execution owners, admins, or superadmins

    **Path Parameters**:
    - ``execution``: Execution ID (UUID format)

    **Success Response** (200):

    .. code-block:: json

        {
          "data": {
            "execution_id": "550e8400-e29b-41d4-a716-446655440000",
            "urls": [
              {
                "filename": "soc_550e8400.tif",
                "url": "https://bucket.s3.amazonaws.com/outputs/550e8400/soc_550e8400.tif?X-Amz-...",
                "expires_at": "2025-04-23T15:00:00+00:00"
              }
            ]
          }
        }

    An empty ``urls`` list is returned (not an error) when:
    - ``OUTPUT_S3_BUCKET`` is not configured, or
    - The execution has no S3 output files yet.

    **Error Responses**:
    - ``401 Unauthorized``: No or invalid JWT token
    - ``404 Not Found``: Execution does not exist or user lacks access
    - ``500 Internal Server Error``: Unexpected error
    """
    logger.info(f"[ROUTER]: Get result URLs for execution {execution}")
    try:
        urls = ExecutionService.get_result_presigned_urls(execution, current_user)
    except ExecutionNotFound as exc:
        logger.error("[ROUTER]: " + exc.message)
        return error(status=404, detail=exc.message)
    except Exception as exc:
        logger.error("[ROUTER]: " + str(exc))
        return error(status=500, detail="Generic Error")

    return jsonify(data={"execution_id": execution, "urls": urls}), 200
```

#### Step 5: Tests — `tests/test_presigned_result_urls.py`

Create a new test file. Model the structure after `tests/test_execution_logs_endpoints.py`.

```python
"""
Tests for GET /api/v1/execution/<id>/result-urls
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from gefapi import db


@pytest.fixture
def execution_id(app, sample_execution):
    with app.app_context():
        execution = db.session.merge(sample_execution)
        return str(execution.id)


FAKE_PRESIGNED_URL = (
    "https://test-output-bucket.s3.amazonaws.com/outputs/exec-id/file.tif"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=3600&X-Amz-Signature=abc123"
)

MOCK_KEYS = ["outputs/exec-id/soc_result.tif", "outputs/exec-id/lc_result.tif"]


@pytest.mark.usefixtures("client")
class TestGetExecutionResultUrls:
    """Tests for GET /api/v1/execution/<id>/result-urls"""

    def test_requires_auth(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/execution/{fake_id}/result-urls")
        assert resp.status_code == 401

    def test_nonexistent_execution_returns_404(self, client, auth_headers_user):
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/execution/{fake_id}/result-urls",
            headers=auth_headers_user,
        )
        assert resp.status_code == 404

    @patch("gefapi.services.execution_service.SETTINGS", new_callable=dict)
    def test_returns_empty_list_when_bucket_not_configured(
        self, mock_settings, client, auth_headers_user, execution_id, app
    ):
        # Patch SETTINGS to have no OUTPUT_S3_BUCKET
        from gefapi.config import SETTINGS as real_settings

        mock_settings.update(real_settings)
        mock_settings["OUTPUT_S3_BUCKET"] = None

        resp = client.get(
            f"/api/v1/execution/{execution_id}/result-urls",
            headers=auth_headers_user,
        )
        assert resp.status_code == 200
        data = resp.json["data"]
        assert data["urls"] == []

    @patch("gefapi.s3.generate_presigned_url", return_value=FAKE_PRESIGNED_URL)
    @patch("gefapi.s3.list_execution_result_keys", return_value=MOCK_KEYS)
    @patch.dict(
        "gefapi.services.execution_service.SETTINGS",
        {
            "OUTPUT_S3_BUCKET": "test-output-bucket",
            "OUTPUT_S3_PREFIX": "outputs",
            "PRESIGNED_URL_EXPIRY_SECONDS": 3600,
        },
    )
    def test_owner_gets_presigned_urls(
        self, mock_list, mock_presign, client, auth_headers_user, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/result-urls",
            headers=auth_headers_user,
        )
        assert resp.status_code == 200
        data = resp.json["data"]
        assert data["execution_id"] == execution_id
        assert len(data["urls"]) == 2
        for item in data["urls"]:
            assert "filename" in item
            assert "url" in item
            assert "expires_at" in item
            assert "X-Amz" in item["url"]  # confirms it's a presigned URL

    @patch("gefapi.s3.generate_presigned_url", return_value=FAKE_PRESIGNED_URL)
    @patch("gefapi.s3.list_execution_result_keys", return_value=MOCK_KEYS)
    @patch.dict(
        "gefapi.services.execution_service.SETTINGS",
        {
            "OUTPUT_S3_BUCKET": "test-output-bucket",
            "OUTPUT_S3_PREFIX": "outputs",
            "PRESIGNED_URL_EXPIRY_SECONDS": 3600,
        },
    )
    def test_admin_can_get_urls_for_any_execution(
        self, mock_list, mock_presign, client, auth_headers_admin, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/result-urls",
            headers=auth_headers_admin,
        )
        assert resp.status_code == 200
        assert len(resp.json["data"]["urls"]) == 2

    @patch("gefapi.s3.list_execution_result_keys", return_value=[])
    @patch.dict(
        "gefapi.services.execution_service.SETTINGS",
        {
            "OUTPUT_S3_BUCKET": "test-output-bucket",
            "OUTPUT_S3_PREFIX": "outputs",
            "PRESIGNED_URL_EXPIRY_SECONDS": 3600,
        },
    )
    def test_returns_empty_list_when_no_files(
        self, mock_list, client, auth_headers_user, execution_id
    ):
        resp = client.get(
            f"/api/v1/execution/{execution_id}/result-urls",
            headers=auth_headers_user,
        )
        assert resp.status_code == 200
        assert resp.json["data"]["urls"] == []
```

---

### Phase 2 — QGIS Plugin (trends.earth)

#### Step 6: `LDMP/jobs/manager.py` — Add `_get_result_presigned_urls` helper

Add a new method to the `JobManager` class (or the class that contains `_download_result`).
Returns both the `{filename → url}` mapping **and** a parsed `expires_at` datetime so the
download loop can check for impending expiry before each file.

```python
import typing
from datetime import datetime, timedelta, timezone

# Refresh URL bundle this many seconds before expiry to avoid race conditions
_PRESIGNED_URL_REFRESH_MARGIN = timedelta(seconds=120)  # 2-minute safety buffer


def _get_result_presigned_urls(
    self, job_id: uuid.UUID
) -> tuple[dict[str, str], typing.Optional[datetime]]:
    """Fetch pre-signed download URLs for all S3 output files of a job.

    Calls GET /api/v1/execution/{id}/result-urls (JWT-authenticated).

    Returns:
        A 2-tuple of:
        - dict mapping filename → presigned URL (empty on any error)
        - datetime (UTC) when the URLs expire, or None if unavailable

    On any error, returns ({}, None) so the caller falls back to the
    original URL without interrupting the download.
    """
    try:
        response = self.api_client.call_api(
            f"/api/v1/execution/{job_id}/result-urls",
            method="get",
            use_token=True,
        )
        items = response.get("data", {}).get("urls", [])
        if not items:
            return {}, None
        url_map = {item["filename"]: item["url"] for item in items}
        # All items share the same expires_at; parse from the first one
        expires_at: typing.Optional[datetime] = None
        raw_expiry = items[0].get("expires_at")
        if raw_expiry:
            expires_at = datetime.fromisoformat(raw_expiry)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        return url_map, expires_at
    except Exception:
        log(f"[WARNING]: Could not fetch pre-signed URLs for job {job_id}")
        return {}, None
```

#### Step 7: `LDMP/jobs/manager.py` — Update `_download_cloud_results` (~line 2414)

Fetch the URL bundle once at the start of the download loop, then re-fetch whenever the URLs
are about to expire before starting the next file. Also retry once with a fresh URL if a
download fails (handles the race condition where the URL expires between the check and the
actual S3 request).

```python
def _download_cloud_results(self, job, result):
    # Fetch presigned URLs once; will refresh if they near expiry
    presigned_urls, expires_at = self._get_result_presigned_urls(job.id)

    for tile_or_raster in tiles:  # adapt to actual loop structure
        original_url = ...  # existing URL from execution.results
        output_path = ...  # existing output path logic
        expected_etag = ...  # existing etag logic

        filename = Path(original_url).name  # or however filename is extracted

        # --- Expiry check: refresh if within the safety margin ---
        if expires_at is not None:
            now = datetime.now(timezone.utc)
            if now >= expires_at - _PRESIGNED_URL_REFRESH_MARGIN:
                log(
                    f"[INFO]: Pre-signed URLs for job {job.id} nearing expiry "
                    f"(expires {expires_at.isoformat()}); refreshing before next download"
                )
                presigned_urls, expires_at = self._get_result_presigned_urls(job.id)

        download_url = presigned_urls.get(filename) or original_url

        success = self._download_result(download_url, output_path, expected_etag)

        # --- Retry-on-expiry: if download failed and we used a presigned URL,
        #     the URL may have expired in the gap between the check and the
        #     S3 request.  Refresh once and retry. ---
        if not success and presigned_urls.get(filename):
            log(
                f"[WARNING]: Download failed for {filename}; "
                "refreshing pre-signed URLs and retrying once"
            )
            presigned_urls, expires_at = self._get_result_presigned_urls(job.id)
            download_url = presigned_urls.get(filename) or original_url
            success = self._download_result(download_url, output_path, expected_etag)

        if not success:
            # Handle failure as before (existing error handling)
            ...
```

> The exact variable names and loop structure will differ from the pseudocode above — adapt
> to the real loop in `_download_cloud_results`. The key invariant is:
> **check-then-download** before every file, and **refresh-then-retry** on failure when a
> presigned URL was used.

---

### Phase 3 — Infrastructure (manual, done AFTER code is deployed)

#### Step 8: Make `OUTPUT_S3_BUCKET` private

**Do this only after the new endpoint is live in production** to avoid a gap where existing
users cannot download results.

Remove from the bucket policy the statement allowing public `s3:GetObject`:

```json
{
  "Effect": "Allow",
  "Principal": "*",
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::OUTPUT_BUCKET_NAME/*"
}
```

Optionally add an explicit deny as belt-and-suspenders:

```json
{
  "Effect": "Deny",
  "Principal": "*",
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::OUTPUT_BUCKET_NAME/*",
  "Condition": {
    "StringNotEquals": {
      "aws:PrincipalAccount": "<CI_AWS_ACCOUNT_ID>"
    }
  }
}
```

Ensure the API's IAM role has `s3:GetObject`, `s3:ListBucket`, and `s3:ListObjectsV2` on
`OUTPUT_S3_BUCKET`.

---

## Verification Checklist

1. `./run_tests.sh` in `trends.earth-API` — all existing tests pass + new tests in
   `tests/test_presigned_result_urls.py` pass.
2. `poetry run ruff check gefapi/ tests/` — no errors.
3. `poetry run ruff format --check gefapi/ tests/` — no errors.
4. Manual test: call `GET /api/v1/execution/<real-id>/result-urls` with valid JWT, verify
   response contains `X-Amz-Signature` and `X-Amz-Expires` query params in URLs.
5. Manual test: unauthenticated request returns `401`.
6. Manual test: authenticated request for another user's execution returns `404`.
7. After infrastructure step: verify direct S3 URL (without pre-signed params) returns `403`.
8. QGIS plugin integration: download a completed openEO execution with private bucket — files
   land in expected local path.
9. QGIS plugin expiry scenario: simulate a download session that spans the expiry window by
   mocking `datetime.now()` to return a time just before expiry for the first file and just
   after for the second — verify `_get_result_presigned_urls` is called a second time and the
   second file still downloads successfully.

---

## Key File Reference

| File | Purpose |
|------|---------|
| `gefapi/config/base.py` | Add `PRESIGNED_URL_EXPIRY_SECONDS` (top-level SETTINGS key) |
| `gefapi/s3.py` | Add `list_execution_result_keys()` and `generate_presigned_url()` |
| `gefapi/services/execution_service.py` | Add `ExecutionService.get_result_presigned_urls()` static method |
| `gefapi/routes/api/v1/executions.py:891` | Add `GET /execution/<id>/result-urls` endpoint after `get_download_results` |
| `tests/test_presigned_result_urls.py` | New test file (create, do not modify existing) |
| `trends.earth/LDMP/jobs/manager.py:2414` | Update `_download_cloud_results` to use presigned URLs |
| `trends.earth/LDMP/jobs/manager.py` | Add `_get_result_presigned_urls` helper method |

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Pre-signed URL expiry = 1 hour (default) | Reasonable default; operators with slow connections or very large outputs should set `PRESIGNED_URL_EXPIRY_SECONDS` higher (e.g. `86400` = 24 h) in their env file |
| Per-file expiry check with 2-minute margin (`_PRESIGNED_URL_REFRESH_MARGIN`) | Prevents using a URL that will expire mid-request; margin covers typical S3 round-trip latency |
| Retry-on-failure with URL refresh (once) | Handles race condition where URL expires between the expiry check and the actual S3 GET request (e.g. slow DNS, GIL delay) |
| `execution.results` JSONB **not modified** | S3 URIs stay as-is in DB; presigned URLs are ephemeral and generated on-demand only |
| Empty list (not error) when `OUTPUT_S3_BUCKET` is unset | Backward compatibility for deployments that don't use `OUTPUT_S3_BUCKET` |
| QGIS plugin falls back to original URL on failure | Supports transition period; old public-bucket executions still work |
| Ownership check reuses `get_execution(id, user)` | Single source of truth for authorization; returns 404 (not 403) to avoid confirming existence |
| `PARAMS_S3_BUCKET` excluded from scope | Already private; content is fetched server-side only, never directly accessed by clients |
