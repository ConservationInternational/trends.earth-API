import logging

import boto3
import botocore
import rollbar

from gefapi.config import SETTINGS


def push_script_to_s3(file_path, object_basename):
    object_name = SETTINGS.get('SCRIPTS_S3_PREFIX') + '/' + object_basename
    logging.info('[SERVICE]: Saving %s to S3', object_name)
    s3_client = boto3.client('s3')
    try:
        _ = s3_client.upload_file(str(file_path),
                                  SETTINGS.get('SCRIPTS_S3_BUCKET'),
                                  object_name)
    except botocore.exceptions.ClientError as e:
        logging.error(e)
        rollbar.report_exc_info()
        return False
    return True


def get_script_from_s3(script_file, out_path):
    object_name = SETTINGS.get('SCRIPTS_S3_PREFIX') + '/' + script_file
    s3 = boto3.client('s3')
    s3.download_file(SETTINGS.get('SCRIPTS_S3_BUCKET'), object_name, out_path)


def push_params_to_s3(file_path, object_basename):
    object_name = SETTINGS.get('PARAMS_S3_PREFIX') + '/' + object_basename
    logging.info('[SERVICE]: Saving %s to S3', object_name)
    s3_client = boto3.client('s3')
    try:
        _ = s3_client.upload_file(str(file_path),
                                  SETTINGS.get('PARAMS_S3_BUCKET'),
                                  object_name)
    except botocore.exceptions.ClientError as e:
        logging.error(e)
        rollbar.report_exc_info()
        return False
    return True
