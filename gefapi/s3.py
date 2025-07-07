import logging

import boto3
import botocore
import rollbar

from gefapi.config import SETTINGS

logger = logging.getLogger()


def push_script_to_s3(file_path, object_basename):
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
