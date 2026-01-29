#!/usr/bin/env python3
"""
EC2 Instance Role Setup Script for CodeDeploy

This script creates an IAM role for EC2 instances that will run the
Trends.Earth API. The role allows the instance to:
- Access ECR to pull Docker images
- Access S3 to retrieve deployment packages
- Communicate with CodeDeploy

Usage:
    python setup_ec2_instance_role.py [--profile PROFILE] [--region REGION]
"""

import boto3
import argparse
import json
import sys
from botocore.exceptions import ClientError

ROLE_NAME = 'TrendsEarthEC2CodeDeploy'
INSTANCE_PROFILE_NAME = 'TrendsEarthEC2CodeDeploy'


def create_clients(profile=None, region=None):
    """Create and return AWS service clients."""
    session_args = {}
    if profile:
        session_args['profile_name'] = profile
    if region:
        session_args['region_name'] = region
    
    session = boto3.Session(**session_args)
    return {
        'iam': session.client('iam'),
        'sts': session.client('sts')
    }


def get_account_info(sts_client):
    """Get the current AWS account information."""
    identity = sts_client.get_caller_identity()
    return identity['Account']


def create_role(iam_client, account_id, region):
    """Create the EC2 instance role."""
    # Trust policy for EC2
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "ec2.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    try:
        response = iam_client.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description='Role for EC2 instances running Trends.Earth API with CodeDeploy',
            Tags=[
                {'Key': 'Project', 'Value': 'TrendsEarthAPI'},
                {'Key': 'ManagedBy', 'Value': 'automation'}
            ]
        )
        role_arn = response['Role']['Arn']
        print(f"✅ Created IAM role: {role_arn}")
        return role_arn
    except ClientError as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            response = iam_client.get_role(RoleName=ROLE_NAME)
            role_arn = response['Role']['Arn']
            print(f"ℹ️  IAM role already exists: {role_arn}")
            return role_arn
        raise


def attach_policies(iam_client, account_id, region):
    """Attach required policies to the role."""
    # Attach AWS managed policies
    managed_policies = [
        # CodeDeploy agent needs this
        'arn:aws:iam::aws:policy/service-role/AmazonEC2RoleforAWSCodeDeploy',
        # For SSM (optional but useful)
        'arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore'
    ]
    
    for policy_arn in managed_policies:
        try:
            iam_client.attach_role_policy(
                RoleName=ROLE_NAME,
                PolicyArn=policy_arn
            )
            print(f"✅ Attached policy: {policy_arn.split('/')[-1]}")
        except ClientError as e:
            if e.response['Error']['Code'] != 'EntityAlreadyExists':
                print(f"⚠️  Could not attach policy {policy_arn}: {e}")
    
    # Create and attach custom policy for ECR and S3 access
    custom_policy_name = 'TrendsEarthEC2DeploymentPolicy'
    custom_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECRAuthToken",
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken"
                ],
                "Resource": "*"
            },
            {
                "Sid": "ECRPull",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage"
                ],
                "Resource": f"arn:aws:ecr:{region}:{account_id}:repository/trendsearth-api"
            },
            {
                "Sid": "S3DeploymentBucket",
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:ListBucket"
                ],
                "Resource": [
                    f"arn:aws:s3:::trendsearth-api-deployments",
                    f"arn:aws:s3:::trendsearth-api-deployments/*"
                ]
            },
            {
                "Sid": "CloudWatchLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams"
                ],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/codedeploy/*"
            }
        ]
    }
    
    try:
        iam_client.create_policy(
            PolicyName=custom_policy_name,
            PolicyDocument=json.dumps(custom_policy),
            Description='Custom policy for Trends.Earth API EC2 instances',
            Tags=[
                {'Key': 'Project', 'Value': 'TrendsEarthAPI'},
                {'Key': 'ManagedBy', 'Value': 'automation'}
            ]
        )
        print(f"✅ Created custom policy: {custom_policy_name}")
    except ClientError as e:
        if e.response['Error']['Code'] != 'EntityAlreadyExists':
            print(f"⚠️  Could not create custom policy: {e}")
    
    # Attach custom policy
    try:
        iam_client.attach_role_policy(
            RoleName=ROLE_NAME,
            PolicyArn=f"arn:aws:iam::{account_id}:policy/{custom_policy_name}"
        )
        print(f"✅ Attached custom policy: {custom_policy_name}")
    except ClientError as e:
        print(f"⚠️  Could not attach custom policy: {e}")


def create_instance_profile(iam_client):
    """Create instance profile and attach role."""
    try:
        iam_client.create_instance_profile(
            InstanceProfileName=INSTANCE_PROFILE_NAME,
            Tags=[
                {'Key': 'Project', 'Value': 'TrendsEarthAPI'},
                {'Key': 'ManagedBy', 'Value': 'automation'}
            ]
        )
        print(f"✅ Created instance profile: {INSTANCE_PROFILE_NAME}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            print(f"ℹ️  Instance profile already exists: {INSTANCE_PROFILE_NAME}")
        else:
            raise
    
    # Add role to instance profile
    try:
        iam_client.add_role_to_instance_profile(
            InstanceProfileName=INSTANCE_PROFILE_NAME,
            RoleName=ROLE_NAME
        )
        print(f"✅ Added role to instance profile")
    except ClientError as e:
        if e.response['Error']['Code'] == 'LimitExceeded':
            print(f"ℹ️  Role already attached to instance profile")
        else:
            print(f"⚠️  Could not add role to instance profile: {e}")


def main(profile=None, region=None):
    """Main function to set up EC2 instance role."""
    print("🚀 Setting up EC2 Instance Role for Trends.Earth API...")
    print("=" * 60)
    
    # Create AWS clients
    clients = create_clients(profile, region)
    
    # Get account info
    account_id = get_account_info(clients['sts'])
    actual_region = region or 'us-east-1'
    print(f"📋 AWS Account ID: {account_id}")
    print(f"📋 AWS Region: {actual_region}")
    
    # Create role
    print("\n📋 Creating IAM role...")
    role_arn = create_role(clients['iam'], account_id, actual_region)
    
    # Attach policies
    print("\n📋 Attaching policies...")
    attach_policies(clients['iam'], account_id, actual_region)
    
    # Create instance profile
    print("\n📋 Creating instance profile...")
    create_instance_profile(clients['iam'])
    
    # Summary
    print("\n" + "=" * 60)
    print("✅ EC2 Instance Role Setup Complete!")
    print("=" * 60)
    
    print("\n📋 Created Resources:")
    print(f"  - IAM Role: {ROLE_NAME}")
    print(f"  - Instance Profile: {INSTANCE_PROFILE_NAME}")
    
    print("\n📋 Attached Policies:")
    print("  - AmazonEC2RoleforAWSCodeDeploy (AWS managed)")
    print("  - AmazonSSMManagedInstanceCore (AWS managed)")
    print("  - TrendsEarthEC2DeploymentPolicy (Custom)")
    
    print("\n📋 Permissions Granted:")
    print("  - ECR: Pull Docker images from trendsearth-api repository")
    print("  - S3: Read deployment packages from trendsearth-api-deployments bucket")
    print("  - CloudWatch: Write logs for CodeDeploy operations")
    print("  - SSM: Managed instance core for optional SSH alternative")
    
    print("\n📋 Next Steps:")
    print("1. Attach the instance profile to your EC2 instances:")
    print(f"   aws ec2 associate-iam-instance-profile \\")
    print(f"       --instance-id <YOUR_INSTANCE_ID> \\")
    print(f"       --iam-instance-profile Name={INSTANCE_PROFILE_NAME}")
    print("")
    print("   Or when launching a new instance, specify:")
    print(f"   --iam-instance-profile Name={INSTANCE_PROFILE_NAME}")
    print("")
    print("2. For existing instances with a different profile, replace it:")
    print(f"   # First, get the current association ID")
    print(f"   aws ec2 describe-iam-instance-profile-associations \\")
    print(f"       --filters Name=instance-id,Values=<YOUR_INSTANCE_ID>")
    print(f"   # Then replace it")
    print(f"   aws ec2 replace-iam-instance-profile-association \\")
    print(f"       --association-id <ASSOCIATION_ID> \\")
    print(f"       --iam-instance-profile Name={INSTANCE_PROFILE_NAME}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set up EC2 instance role")
    parser.add_argument("--profile", "-p", help="AWS profile to use")
    parser.add_argument("--region", "-r", default="us-east-1", help="AWS region")
    args = parser.parse_args()
    
    try:
        main(profile=args.profile, region=args.region)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
