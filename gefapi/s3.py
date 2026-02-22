import logging
import posixpath

import boto3
import botocore
import rollbar

from gefapi.config import SETTINGS

logger = logging.getLogger()


def _sanitize_object_basename(object_basename: str) -> str:
    """Sanitize an S3 object basename to prevent path traversal attacks.

    Strips directory components and rejects values containing path traversal
    sequences (e.g. '..', '/', '\\') to ensure objects stay within the
    intended S3 prefix.

    Args:
        object_basename: The basename to sanitize.

    Returns:
        The sanitized basename.

    Raises:
        ValueError: If the basename is empty, contains traversal sequences,
            or resolves to a different name after sanitization.
    """
    if not object_basename:
        raise ValueError("object_basename must not be empty")

    # Reject obvious traversal attempts before any normalization
    if ".." in object_basename:
        raise ValueError(
            f"Invalid object_basename: path traversal detected: {object_basename!r}"
        )

    # Extract just the filename, stripping any directory components
    # Use posixpath since S3 keys use forward slashes
    sanitized = posixpath.basename(object_basename)

    # Also strip Windows-style directory separators
    if "\\" in object_basename:
        sanitized = sanitized.split("\\")[-1]

    if not sanitized:
        raise ValueError(
            f"Invalid object_basename: resolved to empty after sanitization: "
            f"{object_basename!r}"
        )

    if sanitized != object_basename:
        logger.warning(
            "S3 object_basename sanitized: %r -> %r", object_basename, sanitized
        )

    return sanitized


def push_script_to_s3(file_path, object_basename):
    object_basename = _sanitize_object_basename(object_basename)

    prefix = SETTINGS.get("SCRIPTS_S3_PREFIX")
    bucket = SETTINGS.get("SCRIPTS_S3_BUCKET")

    if not prefix:
        raise ValueError("SCRIPTS_S3_PREFIX configuration is required")
    if not bucket:
        raise ValueError("SCRIPTS_S3_BUCKET configuration is required")

    object_name = prefix + "/" + object_basename
    logger.info("[SERVICE]: Saving %s to S3", object_name)
    s3_client = boto3.client("s3")
    try:
        _ = s3_client.upload_file(str(file_path), bucket, object_name)
    except botocore.exceptions.ClientError as e:
        logger.error(e)
        rollbar.report_exc_info()
        return False
    return True


def get_script_from_s3(script_file, out_path):
    script_file = _sanitize_object_basename(script_file)

    prefix = SETTINGS.get("SCRIPTS_S3_PREFIX")
    bucket = SETTINGS.get("SCRIPTS_S3_BUCKET")

    if not prefix:
        raise ValueError("SCRIPTS_S3_PREFIX configuration is required")
    if not bucket:
        raise ValueError("SCRIPTS_S3_BUCKET configuration is required")

    object_name = prefix + "/" + script_file
    s3 = boto3.client("s3")
    s3.download_file(bucket, object_name, out_path)


def delete_script_from_s3(object_basename):
    object_basename = _sanitize_object_basename(object_basename)

    prefix = SETTINGS.get("SCRIPTS_S3_PREFIX")
    bucket = SETTINGS.get("SCRIPTS_S3_BUCKET")

    if not prefix:
        raise ValueError("SCRIPTS_S3_PREFIX configuration is required")
    if not bucket:
        raise ValueError("SCRIPTS_S3_BUCKET configuration is required")

    object_name = prefix + "/" + object_basename
    logger.info("[SERVICE]: Deleting %s from S3", object_name)
    s3_client = boto3.client("s3")
    try:
        s3_client.delete_object(Bucket=bucket, Key=object_name)
    except botocore.exceptions.ClientError as e:
        logger.error(e)
        rollbar.report_exc_info()
        return False
    return True


def push_params_to_s3(file_path, object_basename):
    object_basename = _sanitize_object_basename(object_basename)

    prefix = SETTINGS.get("PARAMS_S3_PREFIX")
    bucket = SETTINGS.get("PARAMS_S3_BUCKET")

    if not prefix:
        raise ValueError("PARAMS_S3_PREFIX configuration is required")
    if not bucket:
        raise ValueError("PARAMS_S3_BUCKET configuration is required")

    object_name = prefix + "/" + object_basename
    logger.info("[SERVICE]: Saving %s to S3", object_name)
    s3_client = boto3.client("s3")
    try:
        _ = s3_client.upload_file(str(file_path), bucket, object_name)
    except botocore.exceptions.ClientError as e:
        logger.error(e)
        rollbar.report_exc_info()
        return False
    return True
