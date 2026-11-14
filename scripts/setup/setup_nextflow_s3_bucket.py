#!/usr/bin/env python3
"""Provision private S3 storage for Nextflow work and state."""

import argparse

import boto3
from botocore.exceptions import ClientError

WORK_RETENTION_DAYS = 365
STATE_RETENTION_DAYS = 365
ENVIRONMENTS = ("production", "staging")


def create_clients(profile=None, region=None):
    """Create S3 and STS clients using the requested AWS profile and region."""
    session_args = {}
    if profile:
        session_args["profile_name"] = profile
    if region:
        session_args["region_name"] = region

    session = boto3.Session(**session_args)
    return {"s3": session.client("s3"), "sts": session.client("sts")}


def lifecycle_configuration():
    """Expire work and state objects and their old versions at separate rates."""
    rules = []
    for environment in ENVIRONMENTS:
        for data_type, retention_days in (
            ("work", WORK_RETENTION_DAYS),
            ("state", STATE_RETENTION_DAYS),
        ):
            prefix = f"{environment}/{data_type}/"
            rules.append(
                {
                    "ID": f"Expire{environment.title()}{data_type.title()}",
                    "Status": "Enabled",
                    "Filter": {"Prefix": prefix},
                    "Expiration": {"Days": retention_days},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                }
            )
            rules.append(
                {
                    "ID": f"Cleanup{environment.title()}{data_type.title()}Markers",
                    "Status": "Enabled",
                    "Filter": {"Prefix": prefix},
                    "Expiration": {"ExpiredObjectDeleteMarker": True},
                }
            )
    return {"Rules": rules}


def create_bucket(s3_client, bucket_name, region):
    """Create the bucket, accepting a bucket already owned in this account."""
    try:
        if region == "us-east-1":
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "BucketAlreadyOwnedByYou":
            print(f"Bucket already exists in this account: {bucket_name}")
            return
        if code == "BucketAlreadyExists":
            raise RuntimeError(
                f"S3 bucket name is already taken by another account: {bucket_name}"
            ) from exc
        raise
    print(f"Created S3 bucket: {bucket_name}")


def configure_bucket(s3_client, bucket_name):
    """Apply private access, encryption, versioning, tags, and expiration rules."""
    s3_client.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3_client.put_bucket_encryption(
        Bucket=bucket_name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    s3_client.put_bucket_versioning(
        Bucket=bucket_name,
        VersioningConfiguration={"Status": "Enabled"},
    )
    s3_client.put_bucket_lifecycle_configuration(
        Bucket=bucket_name,
        LifecycleConfiguration=lifecycle_configuration(),
    )
    s3_client.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={
            "TagSet": [
                {"Key": "Project", "Value": "TrendsEarthAPI"},
                {"Key": "Purpose", "Value": "Nextflow work and state"},
                {"Key": "ManagedBy", "Value": "automation"},
            ]
        },
    )


def main(profile=None, region=None):
    """Create and configure the account-specific Nextflow bucket."""
    clients = create_clients(profile, region)
    account_id = clients["sts"].get_caller_identity()["Account"]
    region = region or boto3.Session(profile_name=profile).region_name or "us-east-1"
    bucket_name = f"trendsearth-api-nextflow-{account_id}"

    create_bucket(clients["s3"], bucket_name, region)
    configure_bucket(clients["s3"], bucket_name)

    print(f"\nNextflow S3 bucket ready: {bucket_name} ({region})")
    print(
        f"Lifecycle retention: work {WORK_RETENTION_DAYS} days, "
        f"state {STATE_RETENTION_DAYS} days"
    )
    for environment in ENVIRONMENTS:
        print(
            f"{environment.title()} GitHub Actions variables:"
            f"\n  {environment.upper()}_NEXTFLOW_WORK_ROOT="
            f"s3://{bucket_name}/{environment}/work"
            f"\n  {environment.upper()}_NEXTFLOW_STATE_ROOT="
            f"s3://{bucket_name}/{environment}/state"
        )
    return bucket_name


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Set up private S3 storage for Nextflow executions"
    )
    parser.add_argument("--profile", "-p", help="AWS profile to use")
    parser.add_argument("--region", "-r", default="us-east-1")
    args = parser.parse_args()
    main(profile=args.profile, region=args.region)
