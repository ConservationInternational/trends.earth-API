#!/usr/bin/env python3
"""
S3 Deployment Bucket Setup Script for Trends.Earth API

This script creates and configures the S3 bucket used to store
deployment packages for CodeDeploy.

Usage:
    python setup_s3_bucket.py [--profile PROFILE]
"""

import boto3
import argparse
import json
import sys
from botocore.exceptions import ClientError


def create_clients(profile=None):
    """Create and return AWS service clients."""
    session_args = {}
    if profile:
        session_args['profile_name'] = profile
    
    session = boto3.Session(**session_args)
    return {
        's3': session.client('s3'),
        'sts': session.client('sts')
    }


def get_account_id(sts_client):
    """Get the current AWS account ID."""
    return sts_client.get_caller_identity()['Account']


def get_region(profile=None):
    """Get the current AWS region."""
    session_args = {}
    if profile:
        session_args['profile_name'] = profile
    session = boto3.Session(**session_args)
    return session.region_name or 'us-east-1'


def create_bucket(s3_client, bucket_name, region):
    """Create an S3 bucket."""
    try:
        if region == 'us-east-1':
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region}
            )
        print(f"✅ Created S3 bucket: {bucket_name}")
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'BucketAlreadyOwnedByYou':
            print(f"ℹ️  Bucket already exists: {bucket_name}")
            return True
        elif e.response['Error']['Code'] == 'BucketAlreadyExists':
            print(f"❌ Bucket name is already taken globally: {bucket_name}")
            return False
        raise


def enable_versioning(s3_client, bucket_name):
    """Enable versioning on the S3 bucket."""
    try:
        s3_client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={'Status': 'Enabled'}
        )
        print(f"✅ Enabled versioning on bucket: {bucket_name}")
    except ClientError as e:
        print(f"❌ Failed to enable versioning: {e}")


def block_public_access(s3_client, bucket_name):
    """Block all public access to the S3 bucket."""
    try:
        s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True
            }
        )
        print(f"✅ Blocked public access on bucket: {bucket_name}")
    except ClientError as e:
        print(f"❌ Failed to block public access: {e}")


def configure_lifecycle_rules(s3_client, bucket_name):
    """Configure lifecycle rules to clean up old deployment packages."""
    lifecycle_config = {
        'Rules': [
            {
                'ID': 'DeleteOldStagingDeployments',
                'Status': 'Enabled',
                'Filter': {'Prefix': 'staging/'},
                'Expiration': {'Days': 30},
                'NoncurrentVersionExpiration': {'NoncurrentDays': 7}
            },
            {
                'ID': 'DeleteOldProductionDeployments',
                'Status': 'Enabled',
                'Filter': {'Prefix': 'production/'},
                'Expiration': {'Days': 90},
                'NoncurrentVersionExpiration': {'NoncurrentDays': 30}
            }
        ]
    }
    
    try:
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_config
        )
        print(f"✅ Configured lifecycle rules on bucket: {bucket_name}")
    except ClientError as e:
        print(f"❌ Failed to configure lifecycle rules: {e}")


def configure_encryption(s3_client, bucket_name):
    """Enable server-side encryption on the bucket."""
    try:
        s3_client.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration={
                'Rules': [
                    {
                        'ApplyServerSideEncryptionByDefault': {
                            'SSEAlgorithm': 'AES256'
                        },
                        'BucketKeyEnabled': True
                    }
                ]
            }
        )
        print(f"✅ Enabled server-side encryption on bucket: {bucket_name}")
    except ClientError as e:
        print(f"❌ Failed to enable encryption: {e}")


def add_bucket_tags(s3_client, bucket_name):
    """Add tags to the bucket."""
    try:
        s3_client.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={
                'TagSet': [
                    {'Key': 'Project', 'Value': 'TrendsEarthAPI'},
                    {'Key': 'Purpose', 'Value': 'CodeDeploy Deployment Packages'},
                    {'Key': 'ManagedBy', 'Value': 'automation'}
                ]
            }
        )
        print(f"✅ Added tags to bucket: {bucket_name}")
    except ClientError as e:
        print(f"❌ Failed to add tags: {e}")


def main(profile=None):
    """Main function to set up S3 deployment bucket."""
    print("🚀 Setting up S3 Deployment Bucket for Trends.Earth API...")
    print("=" * 60)
    
    # Create AWS clients
    clients = create_clients(profile)
    
    # Get account ID and region
    account_id = get_account_id(clients['sts'])
    region = get_region(profile)
    
    print(f"📋 AWS Account ID: {account_id}")
    print(f"📋 AWS Region: {region}")
    
    # Bucket name includes account ID to ensure uniqueness
    bucket_name = f"trendsearth-api-deployments-{account_id}"
    
    print(f"\n📋 Setting up bucket: {bucket_name}")
    
    # Create bucket
    if not create_bucket(clients['s3'], bucket_name, region):
        print("❌ Failed to create S3 bucket. Exiting.")
        sys.exit(1)
    
    # Configure bucket
    print("\n📋 Configuring bucket settings...")
    enable_versioning(clients['s3'], bucket_name)
    block_public_access(clients['s3'], bucket_name)
    configure_encryption(clients['s3'], bucket_name)
    configure_lifecycle_rules(clients['s3'], bucket_name)
    add_bucket_tags(clients['s3'], bucket_name)
    
    # Summary
    print("\n" + "=" * 60)
    print("✅ S3 Bucket Setup Complete!")
    print("=" * 60)
    print(f"\nBucket Name: {bucket_name}")
    print(f"Region: {region}")
    
    print("\n📋 Bucket Configuration:")
    print("  - Versioning: Enabled")
    print("  - Public Access: Blocked")
    print("  - Encryption: AES-256")
    print("  - Lifecycle: Staging (30 days), Production (90 days)")
    
    print("\n📋 Next Steps:")
    print("1. Note the bucket name for GitHub secrets (if needed)")
    print("2. The bucket will be automatically used by the CodeDeploy workflows")
    
    return bucket_name


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set up S3 deployment bucket")
    parser.add_argument("--profile", "-p", help="AWS profile to use")
    args = parser.parse_args()
    
    try:
        main(profile=args.profile)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
