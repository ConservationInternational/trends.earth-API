#!/usr/bin/env python3
"""
GitHub OIDC Provider Setup Script for Trends.Earth API

This script creates the IAM OIDC identity provider and role that allows
GitHub Actions to authenticate with AWS without using long-lived credentials.

Benefits of OIDC over Access Keys:
- No secrets to rotate or manage
- Credentials are short-lived and automatically expire
- Better security posture (no stored credentials to leak)
- Fine-grained access control based on repo/branch/environment

Usage:
    python setup_github_oidc.py [--profile PROFILE]
"""

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

# GitHub's OIDC provider URL and thumbprint
GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
# This is GitHub's OIDC thumbprint - it rarely changes
GITHUB_OIDC_THUMBPRINT = "6938fd4d98bab03faadb97b34396831e3780aea1"

# Configuration
GITHUB_ORG = "ConservationInternational"
GITHUB_REPO = "trends.earth-API"
ROLE_NAME = "GitHubActionsTrendsEarthAPIRole"


def create_clients(profile=None):
    """Create and return AWS service clients."""
    session_args = {}
    if profile:
        session_args["profile_name"] = profile

    session = boto3.Session(**session_args)
    return {"iam": session.client("iam"), "sts": session.client("sts")}


def get_account_id(sts_client):
    """Get the current AWS account ID."""
    return sts_client.get_caller_identity()["Account"]


def create_oidc_provider(iam_client):
    """Create the GitHub OIDC identity provider."""
    try:
        response = iam_client.create_open_id_connect_provider(
            Url=GITHUB_OIDC_URL,
            ClientIDList=["sts.amazonaws.com"],
            ThumbprintList=[GITHUB_OIDC_THUMBPRINT],
            Tags=[
                {"Key": "Project", "Value": "TrendsEarthAPI"},
                {"Key": "Purpose", "Value": "GitHub Actions OIDC"},
                {"Key": "ManagedBy", "Value": "automation"},
            ],
        )
        print(f"✅ Created OIDC provider: {response['OpenIDConnectProviderArn']}")
        return response["OpenIDConnectProviderArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            account_id = boto3.client("sts").get_caller_identity()["Account"]
            oidc_provider = "token.actions.githubusercontent.com"
            arn = f"arn:aws:iam::{account_id}:oidc-provider/{oidc_provider}"
            print(f"ℹ️  OIDC provider already exists: {arn}")
            return arn
        raise


def create_trust_policy(account_id):
    """Create the trust policy for the GitHub Actions role."""
    oidc_provider = "token.actions.githubusercontent.com"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Federated": (
                        f"arn:aws:iam::{account_id}:oidc-provider/{oidc_provider}"
                    )
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {f"{oidc_provider}:aud": "sts.amazonaws.com"},
                    "StringLike": {
                        f"{oidc_provider}:sub": (f"repo:{GITHUB_ORG}/{GITHUB_REPO}:*")
                    },
                },
            }
        ],
    }


def create_deployment_policy(account_id):
    """Create the permissions policy for GitHub Actions deployments."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3DeploymentBucket",
                "Effect": "Allow",
                "Action": [
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                ],
                "Resource": [
                    f"arn:aws:s3:::trendsearth-api-deployments-{account_id}",
                    f"arn:aws:s3:::trendsearth-api-deployments-{account_id}/*",
                ],
            },
            {
                "Sid": "CodeDeployManagement",
                "Effect": "Allow",
                "Action": [
                    "codedeploy:CreateDeployment",
                    "codedeploy:GetDeployment",
                    "codedeploy:GetDeploymentConfig",
                    "codedeploy:GetApplicationRevision",
                    "codedeploy:RegisterApplicationRevision",
                    "codedeploy:GetApplication",
                    "codedeploy:ListDeploymentGroups",
                    "codedeploy:ListDeployments",
                    "codedeploy:GetDeploymentGroup",
                    "codedeploy:BatchGetDeployments",
                    "codedeploy:BatchGetDeploymentGroups",
                    "codedeploy:ListDeploymentTargets",
                    "codedeploy:GetDeploymentTarget",
                    "codedeploy:StopDeployment",
                ],
                "Resource": [
                    "arn:aws:codedeploy:*:*:application:trendsearth-api",
                    "arn:aws:codedeploy:*:*:deploymentgroup:trendsearth-api/*",
                    "arn:aws:codedeploy:*:*:deploymentconfig:*",
                ],
            },
            {
                "Sid": "ECRAuth",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Sid": "ECRRepository",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                    "ecr:CreateRepository",
                    "ecr:DescribeRepositories",
                    "ecr:TagResource",
                ],
                "Resource": ["arn:aws:ecr:*:*:repository/trendsearth-api"],
            },
            {
                "Sid": "STSGetCallerIdentity",
                "Effect": "Allow",
                "Action": ["sts:GetCallerIdentity"],
                "Resource": "*",
            },
        ],
    }


def create_iam_role(iam_client, account_id):
    """Create the IAM role for GitHub Actions."""
    trust_policy = create_trust_policy(account_id)

    try:
        response = iam_client.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="IAM role for GitHub Actions to deploy Trends.Earth API",
            Tags=[
                {"Key": "Project", "Value": "TrendsEarthAPI"},
                {"Key": "Purpose", "Value": "GitHub Actions OIDC"},
                {"Key": "ManagedBy", "Value": "automation"},
            ],
        )
        role_arn = response["Role"]["Arn"]
        print(f"✅ Created IAM role: {role_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            role_arn = f"arn:aws:iam::{account_id}:role/{ROLE_NAME}"
            print(f"ℹ️  IAM role already exists: {role_arn}")

            # Update the trust policy
            iam_client.update_assume_role_policy(
                RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust_policy)
            )
            print(f"✅ Updated trust policy for role: {ROLE_NAME}")
        else:
            raise

    # Create and attach the permissions policy
    permissions_policy = create_deployment_policy(account_id)
    policy_name = "GitHubActionsDeploymentPolicy"

    try:
        iam_client.put_role_policy(
            RoleName=ROLE_NAME,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(permissions_policy),
        )
        print(f"✅ Attached permissions policy: {policy_name}")
    except ClientError as e:
        print(f"❌ Error attaching policy: {e}")
        raise

    return f"arn:aws:iam::{account_id}:role/{ROLE_NAME}"


def main(profile=None):
    """Main function to set up GitHub OIDC."""
    print("🚀 Setting up GitHub OIDC for Trends.Earth API...")
    print("=" * 60)

    # Create AWS clients
    clients = create_clients(profile)

    # Get account ID
    account_id = get_account_id(clients["sts"])
    print(f"📋 AWS Account ID: {account_id}")
    print(f"📋 GitHub Repo: {GITHUB_ORG}/{GITHUB_REPO}")

    # Create OIDC provider
    print("\n📋 Creating OIDC Identity Provider...")
    oidc_arn = create_oidc_provider(clients["iam"])

    # Create IAM role
    print("\n📋 Creating IAM Role...")
    role_arn = create_iam_role(clients["iam"], account_id)

    # Summary
    print("\n" + "=" * 60)
    print("✅ GitHub OIDC Setup Complete!")
    print("=" * 60)
    print(f"\nOIDC Provider ARN: {oidc_arn}")
    print(f"IAM Role ARN: {role_arn}")

    print("\n📋 Next Steps:")
    print("1. Add the following secret to your GitHub repository:")
    print(f"   AWS_OIDC_ROLE_ARN = {role_arn}")
    print("\n2. Run the other setup scripts:")
    print("   python setup_s3_bucket.py")
    print("   python setup_ecr_repositories.py")
    print("   python setup_codedeploy.py")
    print("   python setup_ec2_instance_role.py")

    print("\n📋 Benefits of OIDC Authentication:")
    print("  - No long-lived AWS credentials to manage or rotate")
    print("  - Credentials automatically expire after workflow completes")
    print("  - Better security posture (no stored secrets to leak)")
    print("  - Audit trail in CloudTrail tied to GitHub workflow runs")

    return role_arn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set up GitHub OIDC for AWS")
    parser.add_argument("--profile", "-p", help="AWS profile to use")
    args = parser.parse_args()

    try:
        main(profile=args.profile)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
