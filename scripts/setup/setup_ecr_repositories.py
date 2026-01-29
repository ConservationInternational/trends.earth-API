#!/usr/bin/env python3
"""
ECR Repository Setup Script for Trends.Earth API

This script creates ECR repositories with appropriate lifecycle policies
for storing Docker images.

Usage:
    python setup_ecr_repositories.py [--profile PROFILE] [--region REGION]
"""

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

# Repository configuration
REPOSITORIES = [
    {"name": "trendsearth-api", "description": "Trends.Earth API Docker images"}
]


def create_clients(profile=None, region=None):
    """Create and return AWS service clients."""
    session_args = {}
    if profile:
        session_args["profile_name"] = profile
    if region:
        session_args["region_name"] = region

    session = boto3.Session(**session_args)
    return {"ecr": session.client("ecr"), "sts": session.client("sts")}


def get_account_id(sts_client):
    """Get the current AWS account ID."""
    return sts_client.get_caller_identity()["Account"]


def create_repository(ecr_client, repo_name, description):
    """Create an ECR repository."""
    try:
        response = ecr_client.create_repository(
            repositoryName=repo_name,
            imageScanningConfiguration={"scanOnPush": True},
            imageTagMutability="MUTABLE",
            encryptionConfiguration={"encryptionType": "AES256"},
            tags=[
                {"Key": "Project", "Value": "TrendsEarthAPI"},
                {"Key": "Purpose", "Value": description},
                {"Key": "ManagedBy", "Value": "automation"},
            ],
        )
        repo_uri = response["repository"]["repositoryUri"]
        print(f"✅ Created ECR repository: {repo_uri}")
        return repo_uri
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryAlreadyExistsException":
            # Get the existing repository URI
            response = ecr_client.describe_repositories(repositoryNames=[repo_name])
            repo_uri = response["repositories"][0]["repositoryUri"]
            print(f"ℹ️  Repository already exists: {repo_uri}")
            return repo_uri
        raise


def set_lifecycle_policy(ecr_client, repo_name):
    """Set lifecycle policy to clean up old images."""
    lifecycle_policy = {
        "rules": [
            {
                "rulePriority": 1,
                "description": "Keep last 10 production images",
                "selection": {
                    "tagStatus": "tagged",
                    "tagPrefixList": ["production-"],
                    "countType": "imageCountMoreThan",
                    "countNumber": 10,
                },
                "action": {"type": "expire"},
            },
            {
                "rulePriority": 2,
                "description": "Keep last 5 staging images",
                "selection": {
                    "tagStatus": "tagged",
                    "tagPrefixList": ["staging-"],
                    "countType": "imageCountMoreThan",
                    "countNumber": 5,
                },
                "action": {"type": "expire"},
            },
            {
                "rulePriority": 3,
                "description": "Remove untagged images older than 1 day",
                "selection": {
                    "tagStatus": "untagged",
                    "countType": "sinceImagePushed",
                    "countUnit": "days",
                    "countNumber": 1,
                },
                "action": {"type": "expire"},
            },
            {
                "rulePriority": 4,
                "description": "Keep only last 20 images total",
                "selection": {
                    "tagStatus": "any",
                    "countType": "imageCountMoreThan",
                    "countNumber": 20,
                },
                "action": {"type": "expire"},
            },
        ]
    }

    try:
        ecr_client.put_lifecycle_policy(
            repositoryName=repo_name, lifecyclePolicyText=json.dumps(lifecycle_policy)
        )
        print(f"✅ Set lifecycle policy for repository: {repo_name}")
    except ClientError as e:
        print(f"❌ Failed to set lifecycle policy: {e}")


def set_repository_policy(ecr_client, repo_name, account_id):
    """Set repository policy to allow access from the EC2 instance role."""
    repository_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowEC2Pull",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                "Action": [
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:BatchCheckLayerAvailability",
                ],
            }
        ],
    }

    try:
        ecr_client.set_repository_policy(
            repositoryName=repo_name, policyText=json.dumps(repository_policy)
        )
        print(f"✅ Set repository policy for: {repo_name}")
    except ClientError as e:
        print(f"⚠️  Could not set repository policy (may already be set): {e}")


def main(profile=None, region=None):
    """Main function to set up ECR repositories."""
    print("🚀 Setting up ECR Repositories for Trends.Earth API...")
    print("=" * 60)

    # Create AWS clients
    clients = create_clients(profile, region)

    # Get account ID
    account_id = get_account_id(clients["sts"])
    print(f"📋 AWS Account ID: {account_id}")

    # Get region
    actual_region = clients["ecr"].meta.region_name
    print(f"📋 AWS Region: {actual_region}")

    # Create repositories
    created_repos = []
    for repo_config in REPOSITORIES:
        print(f"\n📋 Setting up repository: {repo_config['name']}")

        repo_uri = create_repository(
            clients["ecr"], repo_config["name"], repo_config["description"]
        )

        if repo_uri:
            set_lifecycle_policy(clients["ecr"], repo_config["name"])
            set_repository_policy(clients["ecr"], repo_config["name"], account_id)
            created_repos.append({"name": repo_config["name"], "uri": repo_uri})

    # Summary
    print("\n" + "=" * 60)
    print("✅ ECR Repository Setup Complete!")
    print("=" * 60)

    print("\n📋 Created Repositories:")
    for repo in created_repos:
        print(f"  - {repo['name']}: {repo['uri']}")

    print("\n📋 Lifecycle Policy:")
    print("  - Keep last 10 production-* images")
    print("  - Keep last 5 staging-* images")
    print("  - Remove untagged images after 1 day")
    print("  - Keep maximum 20 images total")

    print("\n📋 Next Steps:")
    print("1. The workflows will automatically push images to these repositories")
    print("2. Ensure EC2 instance role has permission to pull from ECR")

    return created_repos


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set up ECR repositories")
    parser.add_argument("--profile", "-p", help="AWS profile to use")
    parser.add_argument("--region", "-r", default="us-east-1", help="AWS region")
    args = parser.parse_args()

    try:
        main(profile=args.profile, region=args.region)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
