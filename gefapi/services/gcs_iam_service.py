"""GCS IAM helpers for granting/revoking GEE service-agent bucket access.

When a user runs GEE batch exports under their own OAuth credentials, GEE
executes the export using that project's GEE service agent:

    service-{PROJECT_NUMBER}@gcp-sa-earthengine.iam.gserviceaccount.com

This service agent has no write access to the ``ldmt`` bucket (owned by the
service's GCP project).  The functions here grant and revoke
``roles/storage.objectCreator`` on the output bucket for that service agent,
using the service account credentials stored in ``EE_SERVICE_ACCOUNT_JSON``.

The grant is made once when the user selects their GCP project, and revoked
when they remove their credentials.  All operations are idempotent and
best-effort: failures are logged but never propagated to callers.
"""

import base64
import json
import logging

from gefapi.config.base import SETTINGS

logger = logging.getLogger(__name__)

_GEE_SERVICE_AGENT_TEMPLATE = (
    "serviceAccount:service-{project_number}@gcp-sa-earthengine.iam.gserviceaccount.com"
)
_ROLE = "roles/storage.objectCreator"


def _get_sa_gcs_client():
    """Build a ``google.cloud.storage.Client`` from ``EE_SERVICE_ACCOUNT_JSON``.

    Returns ``None`` (and logs a warning) when the env var is absent or
    cannot be decoded — callers treat this as a non-fatal configuration gap.
    """
    import os

    sa_b64 = SETTINGS.get("environment", {}).get(
        "EE_SERVICE_ACCOUNT_JSON"
    ) or os.getenv("EE_SERVICE_ACCOUNT_JSON")
    if not sa_b64:
        logger.warning(
            "EE_SERVICE_ACCOUNT_JSON is not configured — "
            "cannot manage GCS bucket IAM for OAuth users."
        )
        return None

    try:
        from google.cloud import storage
        from google.oauth2.service_account import Credentials

        decoded = base64.b64decode(sa_b64).decode("utf-8")
        sa_info = json.loads(decoded)
        credentials = Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return storage.Client(
            project=sa_info.get("project_id"),
            credentials=credentials,
        )
    except Exception as exc:
        logger.warning(
            "Failed to build GCS client from EE_SERVICE_ACCOUNT_JSON: %s", exc
        )
        return None


def grant_gee_service_agent_bucket_write(project_number: int, bucket_name: str) -> bool:
    """Grant ``roles/storage.objectCreator`` on *bucket_name* to the GEE
    service agent for *project_number*.

    Idempotent — does nothing if the binding already exists.
    Logs a warning and returns ``False`` on any failure so callers can
    report the outcome without being blocked.
    Returns ``True`` on success (including when the binding already existed).
    """
    member = _GEE_SERVICE_AGENT_TEMPLATE.format(project_number=project_number)
    try:
        client = _get_sa_gcs_client()
        if client is None:
            return False

        bucket = client.bucket(bucket_name)
        policy = bucket.get_iam_policy(requested_policy_version=3)

        # Check whether the binding already exists (idempotency).
        for binding in policy.bindings:
            if binding["role"] == _ROLE and member in binding["members"]:
                logger.info(
                    "IAM binding already exists: %s on gs://%s (%s)",
                    member,
                    bucket_name,
                    _ROLE,
                )
                return True

        policy.bindings.append({"role": _ROLE, "members": {member}})
        bucket.set_iam_policy(policy)
        logger.info("Granted %s to %s on gs://%s", _ROLE, member, bucket_name)
        return True
    except Exception as exc:
        logger.warning(
            "Failed to grant GCS IAM binding for project %s on bucket %s: %s",
            project_number,
            bucket_name,
            exc,
        )
        return False


def revoke_gee_service_agent_bucket_write(
    project_number: int, bucket_name: str
) -> None:
    """Revoke ``roles/storage.objectCreator`` on *bucket_name* from the GEE
    service agent for *project_number*.

    Idempotent — does nothing if no matching binding exists.
    Logs a warning and returns silently on any failure.
    """
    member = _GEE_SERVICE_AGENT_TEMPLATE.format(project_number=project_number)
    try:
        client = _get_sa_gcs_client()
        if client is None:
            return

        bucket = client.bucket(bucket_name)
        policy = bucket.get_iam_policy(requested_policy_version=3)

        new_bindings = []
        removed = False
        for binding in policy.bindings:
            if binding["role"] == _ROLE and member in binding["members"]:
                updated_members = binding["members"] - {member}
                if updated_members:
                    new_bindings.append(
                        {"role": binding["role"], "members": updated_members}
                    )
                removed = True
            else:
                new_bindings.append(binding)

        if not removed:
            logger.info(
                "No IAM binding to revoke for %s on gs://%s", member, bucket_name
            )
            return

        policy.bindings = new_bindings
        bucket.set_iam_policy(policy)
        logger.info("Revoked %s from %s on gs://%s", _ROLE, member, bucket_name)
    except Exception as exc:
        logger.warning(
            "Failed to revoke GCS IAM binding for project %s on bucket %s: %s",
            project_number,
            bucket_name,
            exc,
        )
