# AWS CodeDeploy Deployment Guide

This guide covers setting up and using AWS CodeDeploy for automated deployments of the Trends.Earth API.

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [AWS Infrastructure Setup](#aws-infrastructure-setup)
5. [GitHub Configuration](#github-configuration)
6. [EC2 Instance Setup](#ec2-instance-setup)
7. [Triggering Deployments](#triggering-deployments)
8. [Monitoring and Troubleshooting](#monitoring-and-troubleshooting)
9. [Rollback Procedures](#rollback-procedures)

## Overview

The Trends.Earth API uses AWS CodeDeploy for automated deployments with the following features:

- **Docker buildx** with GitHub Actions cache for fast, efficient builds
- **Amazon ECR** for secure Docker image storage
- **AWS CodeDeploy** for reliable, agent-based deployments
- **OIDC authentication** for secure, credential-less AWS access
- **Docker Swarm** for container orchestration on EC2

### Key Benefits

- **No long-lived credentials**: Uses OIDC federation instead of access keys
- **Pre-built images**: Docker images are built and pushed to ECR before deployment
- **Atomic deployments**: CodeDeploy ensures all-or-nothing deployment updates
- **Automatic rollback**: Failed deployments automatically revert to the previous version
- **Multi-environment support**: Staging and production can run on the same or different instances

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GitHub Actions                                │
├─────────────────────────────────────────────────────────────────────┤
│  1. Build image with Docker buildx + GHA cache                      │
│  2. Push image to Amazon ECR                                        │
│  3. Generate environment file from secrets                          │
│  4. Create deployment package (zip)                                 │
│  5. Upload package to S3                                            │
│  6. Trigger CodeDeploy deployment                                   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        AWS CodeDeploy                                │
├─────────────────────────────────────────────────────────────────────┤
│  1. ApplicationStop: Gracefully stop existing services              │
│  2. BeforeInstall: Clean up and prepare directories                 │
│  3. AfterInstall: Pull Docker images, set permissions               │
│  4. ApplicationStart: Deploy Docker Swarm stack                     │
│  5. ValidateService: Health check verification                      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        EC2 Instance                                  │
├─────────────────────────────────────────────────────────────────────┤
│  Docker Swarm running:                                              │
│  - API service (Gunicorn)                                           │
│  - Worker service (Celery)                                          │
│  - Beat service (Celery scheduler)                                  │
│  - Docker service (Build worker)                                    │
│  - Redis service (Cache/broker)                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

Before setting up CodeDeploy, ensure you have:

1. **AWS Account** with permissions to create IAM roles, ECR repositories, S3 buckets, and CodeDeploy applications
2. **AWS CLI** installed and configured with appropriate credentials
3. **Python 3.x** with `boto3` installed for running setup scripts
4. **GitHub repository** with Actions enabled
5. **EC2 instance(s)** running Ubuntu or Amazon Linux

## AWS Infrastructure Setup

Run the setup scripts in order to create the required AWS resources:

### 1. Setup GitHub OIDC Provider and IAM Role

This creates the trust relationship between GitHub Actions and AWS.

```bash
cd scripts/setup
python setup_github_oidc.py --profile your-profile --region us-east-1
```

**Created resources:**
- GitHub OIDC Identity Provider
- `GitHubActionsDeployRole` IAM role with deployment permissions

### 2. Setup S3 Bucket for Deployment Packages

```bash
python setup_s3_bucket.py --profile your-profile --region us-east-1
```

**Created resources:**
- `trendsearth-api-deployments-{account-id}` S3 bucket with:
  - Versioning enabled
  - AES-256 encryption
  - 30-day lifecycle for old versions

### 3. Setup ECR Repository

```bash
python setup_ecr_repositories.py --profile your-profile --region us-east-1
```

**Created resources:**
- `trendsearth-api` ECR repository with:
  - Image scanning enabled
  - Lifecycle policies for cleanup

### 4. Setup CodeDeploy Application and Deployment Groups

```bash
python setup_codedeploy.py --profile your-profile --region us-east-1
```

**Created resources:**
- `trendsearth-api` CodeDeploy application
- `trendsearth-api-production` deployment group
- `trendsearth-api-staging` deployment group
- `CodeDeployServiceRole` IAM role

### 5. Setup EC2 Instance Role

```bash
python setup_ec2_instance_role.py --profile your-profile --region us-east-1
```

**Created resources:**
- `TrendsEarthEC2CodeDeploy` IAM role
- `TrendsEarthEC2CodeDeploy` instance profile
- Required policies for ECR, S3, and CodeDeploy access

## GitHub Configuration

### Required Secrets

Add these secrets to your GitHub repository (Settings → Secrets and variables → Actions):

#### Core Secrets (Required)

| Secret Name | Description |
|-------------|-------------|
| `AWS_OIDC_ROLE_ARN` | ARN of GitHubActionsDeployRole (from step 1) |
| `SECRET_KEY` | Flask secret key |
| `JWT_SECRET_KEY` | JWT signing key |

#### Staging Environment

| Secret Name | Description |
|-------------|-------------|
| `STAGING_DATABASE_URL` | PostgreSQL connection string |
| `STAGING_REDIS_URL` | Redis connection string |
| `STAGING_API_URL` | API base URL (e.g., https://api-staging.trends.earth) |
| `STAGING_S3_BUCKET_NAME` | S3 bucket for data storage |
| `STAGING_RATE_LIMIT_STORAGE_URI` | Redis URI for rate limiting |

> **Note**: S3 access uses EC2 instance role credentials. Ensure your instance role has permissions for the data bucket.

#### Production Environment

| Secret Name | Description |
|-------------|-------------|
| `PRODUCTION_DATABASE_URL` | PostgreSQL connection string |
| `PRODUCTION_REDIS_URL` | Redis connection string |
| `PRODUCTION_API_URL` | API base URL (e.g., https://api.trends.earth) |
| `PRODUCTION_S3_BUCKET_NAME` | S3 bucket for data storage |
| `PRODUCTION_RATE_LIMIT_STORAGE_URI` | Redis URI for rate limiting |

> **Note**: S3 access uses EC2 instance role credentials. Ensure your instance role has permissions for the data bucket.

#### Common Secrets

| Secret Name | Description |
|-------------|-------------|
| `GEE_SERVICE_ACCOUNT_JSON` | Google Earth Engine service account credentials |
| `ROLLBAR_SCRIPT_TOKEN` | Rollbar error tracking token |
| `SPARKPOST_API_KEY` | SparkPost API key for sending emails |

### Optional Variables

Add these to GitHub Variables for customization:

| Variable Name | Description | Default |
|---------------|-------------|---------|
| `AWS_REGION` | AWS region | us-east-1 |

## EC2 Instance Setup

### 1. Install CodeDeploy Agent

SSH into your EC2 instance and run:

```bash
# Download and run the installation script
curl -O https://raw.githubusercontent.com/ConservationInternational/trends.earth-API/master/scripts/setup/install-codedeploy-agent.sh
chmod +x install-codedeploy-agent.sh
sudo ./install-codedeploy-agent.sh us-east-1  # Replace with your region
```

Or manually:

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y ruby-full wget
cd /tmp
wget https://aws-codedeploy-us-east-1.s3.us-east-1.amazonaws.com/latest/install
chmod +x ./install
sudo ./install auto
sudo systemctl enable codedeploy-agent
sudo systemctl start codedeploy-agent
```

### 2. Attach IAM Instance Profile

```bash
# Associate the instance profile with your EC2 instance
aws ec2 associate-iam-instance-profile \
    --instance-id i-1234567890abcdef0 \
    --iam-instance-profile Name=TrendsEarthEC2CodeDeploy
```

### 3. Tag EC2 Instance for Deployment Group

Add these tags to your EC2 instance(s):

**For Production:**
- Key: `CodeDeploy-TrendsEarth-Production`
- Value: `true`

**For Staging:**
- Key: `CodeDeploy-TrendsEarth-Staging`
- Value: `true`

**Note:** A single instance can have both tags to support both staging and production deployments on the same instance.

### 4. Initialize Docker Swarm

```bash
docker swarm init
```

### 5. Create Application Directories

```bash
# For production
sudo mkdir -p /opt/trendsearth-api-production
sudo chown ubuntu:ubuntu /opt/trendsearth-api-production

# For staging
sudo mkdir -p /opt/trendsearth-api-staging
sudo chown ubuntu:ubuntu /opt/trendsearth-api-staging
```

## Triggering Deployments

### Automatic Deployments

Deployments are triggered automatically when:

- **Staging**: Push to `staging` or `develop` branches
- **Production**: Push to `master` or `main` branches

### Manual Deployments

Use the GitHub Actions workflow dispatch:

1. Go to Actions → "Deploy to Staging (CodeDeploy)" or "Deploy to Production (CodeDeploy)"
2. Click "Run workflow"
3. Optionally specify a different branch
4. Click "Run workflow"

### Deployment Workflow

1. **Build**: Docker image is built with buildx caching
2. **Push**: Image is pushed to ECR with environment-specific tag
3. **Package**: Deployment files are zipped (appspec.yml, env file, scripts)
4. **Upload**: Package is uploaded to S3
5. **Deploy**: CodeDeploy creates a new deployment
6. **Execute**: CodeDeploy agent runs lifecycle hooks on EC2

## Monitoring and Troubleshooting

### View Deployment Status

**GitHub Actions:**
- Check the workflow run in the Actions tab
- Each step shows detailed logs

**AWS Console:**
1. Go to CodeDeploy → Deployments
2. Select your deployment
3. View lifecycle event status

**AWS CLI:**
```bash
# List recent deployments
aws deploy list-deployments \
    --application-name trendsearth-api \
    --deployment-group-name trendsearth-api-production

# Get deployment details
aws deploy get-deployment --deployment-id d-XXXXXXXXX
```

### View Logs on EC2

```bash
# CodeDeploy agent log
sudo tail -f /var/log/aws/codedeploy-agent/codedeploy-agent.log

# Deployment script logs
sudo tail -f /opt/codedeploy-agent/deployment-root/deployment-logs/codedeploy-agent-deployments.log

# Docker service logs
docker service logs trends-earth-production_api --follow
```

### Common Issues

#### Deployment Stuck in "InProgress"

```bash
# Check CodeDeploy agent status
sudo systemctl status codedeploy-agent

# Restart the agent
sudo systemctl restart codedeploy-agent
```

#### ECR Login Failed

Ensure the EC2 instance profile has `ecr:GetAuthorizationToken` permission:

```bash
# Test ECR login manually
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
```

#### Health Check Failed

```bash
# Check if API is responding
curl http://localhost:3001/api-health  # Production
curl http://localhost:3002/api-health  # Staging

# Check Docker service status
docker service ls | grep trends-earth
docker service ps trends-earth-production_api
```

## Rollback Procedures

### Automatic Rollback

Deployments are configured to automatically rollback on failure. If any lifecycle hook fails, CodeDeploy will restore the previous version.

### Manual Rollback

**Option 1: Via AWS Console**
1. Go to CodeDeploy → Deployments
2. Find the last successful deployment
3. Click "Redeploy this revision"

**Option 2: Via AWS CLI**
```bash
# Stop current deployment (if in progress)
aws deploy stop-deployment --deployment-id d-XXXXXXXXX

# Redeploy previous revision
aws deploy create-deployment \
    --application-name trendsearth-api \
    --deployment-group-name trendsearth-api-production \
    --s3-location bucket=trendsearth-api-deployments-<account-id>,key=production/<previous-revision>.zip,bundleType=zip
```

**Option 3: Via GitHub Actions**
1. Find the commit hash of the working version
2. Go to Actions → Select deployment workflow
3. Run workflow with that branch/tag

### Emergency Rollback

If CodeDeploy is not responding:

```bash
# SSH into EC2 instance
ssh ubuntu@your-ec2-instance

# Check running services
docker service ls

# Roll back Docker Swarm service to previous image
docker service update --image <previous-image-uri> trends-earth-production_api
docker service update --image <previous-image-uri> trends-earth-production_worker
docker service update --image <previous-image-uri> trends-earth-production_beat
docker service update --image <previous-image-uri> trends-earth-production_docker
```

## Port Reference

| Environment | API Port | PostgreSQL Port | Notes |
|-------------|----------|-----------------|-------|
| Production  | 3001     | External DB     | |
| Staging     | 3002     | 5433            | Local PostgreSQL |

## Network Configuration

Ensure your security groups allow:

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 443 | TCP | 0.0.0.0/0 | HTTPS (via load balancer) |
| 3001 | TCP | Load balancer | Production API |
| 3002 | TCP | Load balancer | Staging API |
| 2377 | TCP | Docker nodes | Swarm management |
| 7946 | TCP/UDP | Docker nodes | Swarm communication |
| 4789 | UDP | Docker nodes | Overlay network |

## Useful Commands

```bash
# Check deployment history
aws deploy list-deployments --application-name trendsearth-api --deployment-group-name trendsearth-api-production --max-items 10

# Get deployment target health
docker service ps trends-earth-production_api --format "table {{.ID}}\t{{.Name}}\t{{.CurrentState}}\t{{.Error}}"

# Force re-pull images
docker service update --force trends-earth-production_api

# View current image tags
docker service inspect trends-earth-production_api --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'

# Clean up old images
docker image prune -a --filter "until=24h"
```
