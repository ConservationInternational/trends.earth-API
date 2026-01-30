#!/usr/bin/env python3
"""
CodeDeploy Application and Deployment Groups Setup Script

This script creates AWS CodeDeploy application and deployment groups
for Trends.Earth API.

Usage:
    python setup_codedeploy.py [--profile PROFILE] [--region REGION]
"""

import argparse
import sys
import time

import boto3
from botocore.exceptions import ClientError

# CodeDeploy configuration
APPLICATION_NAME = "trendsearth-api"

DEPLOYMENT_GROUPS = [
    {
        "name": "trendsearth-api-production",
        "description": "Production deployment group",
        "environment": "production",
        "ec2_tag_key": "CodeDeploy-TrendsEarth-Production",
        "ec2_tag_value": "true",
    },
    {
        "name": "trendsearth-api-staging",
        "description": "Staging deployment group",
        "environment": "staging",
        "ec2_tag_key": "CodeDeploy-TrendsEarth-Staging",
        "ec2_tag_value": "true",
    },
]


def create_clients(profile=None, region=None):
    """Create and return AWS service clients."""
    session_args = {}
    if profile:
        session_args["profile_name"] = profile
    if region:
        session_args["region_name"] = region

    session = boto3.Session(**session_args)
    return {
        "codedeploy": session.client("codedeploy"),
        "sts": session.client("sts"),
        "iam": session.client("iam"),
    }


def get_account_id(sts_client):
    """Get the current AWS account ID."""
    return sts_client.get_caller_identity()["Account"]


def create_codedeploy_service_role(iam_client):
    """Create or get the CodeDeploy service role."""
    role_name = "CodeDeployServiceRole"

    # Trust policy for CodeDeploy
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "codedeploy.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        # Try to create the role
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=str(trust_policy).replace("'", '"'),
            Description="Service role for AWS CodeDeploy",
            Tags=[
                {"Key": "Project", "Value": "TrendsEarthAPI"},
                {"Key": "ManagedBy", "Value": "automation"},
            ],
        )
        role_arn = response["Role"]["Arn"]
        print(f"✅ Created CodeDeploy service role: {role_arn}")

        # Attach the AWS managed policy
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSCodeDeployRole",
        )
        print("✅ Attached AWSCodeDeployRole policy")

        return role_arn

    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            # Role already exists, get its ARN
            response = iam_client.get_role(RoleName=role_name)
            role_arn = response["Role"]["Arn"]
            print(f"ℹ️  CodeDeploy service role already exists: {role_arn}")
            return role_arn
        raise


def create_application(codedeploy_client):
    """Create CodeDeploy application."""
    try:
        codedeploy_client.create_application(
            applicationName=APPLICATION_NAME,
            computePlatform="Server",
            tags=[
                {"Key": "Project", "Value": "TrendsEarthAPI"},
                {"Key": "ManagedBy", "Value": "automation"},
            ],
        )
        print(f"✅ Created CodeDeploy application: {APPLICATION_NAME}")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ApplicationAlreadyExistsException":
            print(f"ℹ️  CodeDeploy application already exists: {APPLICATION_NAME}")
            return True
        raise


def create_deployment_group(codedeploy_client, group_config, service_role_arn):
    """Create a CodeDeploy deployment group."""
    deployment_config = {
        "applicationName": APPLICATION_NAME,
        "deploymentGroupName": group_config["name"],
        "deploymentConfigName": "CodeDeployDefault.AllAtOnce",
        "ec2TagFilters": [
            {
                "Key": group_config["ec2_tag_key"],
                "Value": group_config["ec2_tag_value"],
                "Type": "KEY_AND_VALUE",
            }
        ],
        "serviceRoleArn": service_role_arn,
        "autoRollbackConfiguration": {
            "enabled": True,
            "events": ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_REQUEST"],
        },
        "deploymentStyle": {
            "deploymentType": "IN_PLACE",
            "deploymentOption": "WITHOUT_TRAFFIC_CONTROL",
        },
        "outdatedInstancesStrategy": "UPDATE",
        "tags": [
            {"Key": "Project", "Value": "TrendsEarthAPI"},
            {"Key": "Environment", "Value": group_config["environment"]},
            {"Key": "ManagedBy", "Value": "automation"},
        ],
    }

    try:
        codedeploy_client.create_deployment_group(**deployment_config)
        print(f"✅ Created deployment group: {group_config['name']}")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "DeploymentGroupAlreadyExistsException":
            print(f"ℹ️  Deployment group already exists: {group_config['name']}")
            # Update the existing deployment group
            try:
                deployment_config["currentDeploymentGroupName"] = group_config["name"]
                del deployment_config["deploymentGroupName"]
                del deployment_config[
                    "tags"
                ]  # Can't update tags with update_deployment_group
                codedeploy_client.update_deployment_group(**deployment_config)
                print(f"✅ Updated existing deployment group: {group_config['name']}")
            except ClientError as update_error:
                print(f"⚠️  Could not update deployment group: {update_error}")
            return True
        raise


def main(profile=None, region=None):
    """Main function to set up CodeDeploy."""
    print("🚀 Setting up AWS CodeDeploy for Trends.Earth API...")
    print("=" * 60)

    # Create AWS clients
    clients = create_clients(profile, region)

    # Get account ID
    account_id = get_account_id(clients["sts"])
    print(f"📋 AWS Account ID: {account_id}")

    # Get region
    actual_region = clients["codedeploy"].meta.region_name
    print(f"📋 AWS Region: {actual_region}")

    # Create CodeDeploy service role
    print("\n📋 Creating CodeDeploy service role...")
    service_role_arn = create_codedeploy_service_role(clients["iam"])

    # Wait for IAM role to propagate (AWS eventual consistency)
    print("\n⏳ Waiting for IAM role to propagate (15 seconds)...")
    time.sleep(15)

    # Create CodeDeploy application
    print("\n📋 Creating CodeDeploy application...")
    create_application(clients["codedeploy"])

    # Create deployment groups
    print("\n📋 Creating deployment groups...")
    for group_config in DEPLOYMENT_GROUPS:
        create_deployment_group(clients["codedeploy"], group_config, service_role_arn)

    # Summary
    print("\n" + "=" * 60)
    print("✅ CodeDeploy Setup Complete!")
    print("=" * 60)

    print("\n📋 Application:")
    print(f"  - Name: {APPLICATION_NAME}")

    print("\n📋 Deployment Groups:")
    for group in DEPLOYMENT_GROUPS:
        print(f"  - {group['name']}")
        print(f"    Environment: {group['environment']}")
        print(f"    EC2 Tag: {group['ec2_tag_key']}={group['ec2_tag_value']}")

    print("\n📋 EC2 Instance Tagging Requirements:")
    print("  For Production instance:")
    print("    Tag Key: CodeDeploy-TrendsEarth-Production")
    print("    Tag Value: true")
    print("  For Staging instance:")
    print("    Tag Key: CodeDeploy-TrendsEarth-Staging")
    print("    Tag Value: true")

    print("\n📋 Next Steps:")
    print("1. Tag your EC2 instances with the appropriate tags above")
    print("2. Ensure the CodeDeploy agent is installed on each EC2 instance")
    print("3. Ensure EC2 instances have the TrendsEarthEC2CodeDeploy role attached")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set up CodeDeploy")
    parser.add_argument("--profile", "-p", help="AWS profile to use")
    parser.add_argument("--region", "-r", default="us-east-1", help="AWS region")
    args = parser.parse_args()

    try:
        main(profile=args.profile, region=args.region)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
