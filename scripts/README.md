# GitHub Actions Deployment Setup

This directory contains scripts to automate the setup of GitHub Actions for secure deployment to EC2 instances with dynamic security group management.

## Overview

The deployment workflow uses a security-first approach:
1. **Dynamic SSH Access**: GitHub Actions runner IP is temporarily added to EC2 security groups
2. **Just-in-Time Permissions**: SSH access is removed after deployment completes
3. **Minimal IAM Permissions**: Custom IAM user with only necessary security group permissions

## Prerequisites

Before running the setup scripts, ensure you have:

1. **AWS CLI** installed and configured with administrative permissions
2. **GitHub CLI** installed and authenticated (`gh auth login`)
3. **jq** installed for JSON processing
4. **SSH access** to your EC2 instances

### Installation Commands

```bash
# Install AWS CLI (Ubuntu/Debian)
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Install GitHub CLI (Ubuntu/Debian)
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update
sudo apt install gh

# Install jq
sudo apt install jq
```

## Setup Process

### Step 1: IAM User and Security Group Setup

The deployment workflow requires an IAM user with specific permissions to manage EC2 security groups for dynamic SSH access. Run the main setup script to create this IAM user and configure GitHub secrets:

```bash
chmod +x scripts/setup-github-deployment.sh
./scripts/setup-github-deployment.sh
```

**What this script does:**
- **Creates IAM User**: `github-actions-deployment` with minimal required permissions
- **IAM Policy**: Creates `GitHubActionsSecurityGroupPolicy` with these permissions:
  - `ec2:AuthorizeSecurityGroupIngress` - Add SSH access rules
  - `ec2:RevokeSecurityGroupIngress` - Remove SSH access rules  
  - `ec2:DescribeSecurityGroups` - Query security group information
- **Scope Limitation**: Permissions are restricted to only the specified staging and production security groups
- **Access Keys**: Generates AWS access keys for the IAM user
- **GitHub Secrets**: Automatically sets all necessary repository secrets

#### Security Group Management Flow

The workflows use this IAM user to:
1. **Before deployment**: Add GitHub Actions runner IP to security group SSH rules
2. **During deployment**: SSH to servers using temporary access
3. **After deployment**: Remove GitHub Actions runner IP from security group SSH rules

This provides **just-in-time SSH access** without leaving security groups permanently open.

#### Required Information

The script will prompt you for:
- **AWS Region** (default: us-east-1)
- **Security Group IDs** for staging and production EC2 instances
- **Docker Registry** URL and HTTP secret (for insecure registries)
- **SSH Configuration** (hosts, usernames, ports)
- **Application Paths** on the servers

### Step 2: SSH Key Setup

Run the SSH key setup script to configure authentication:

```bash
chmod +x scripts/setup-ssh-keys.sh
./scripts/setup-ssh-keys.sh
```

This script provides options to:
- Use existing SSH private key files
- Generate new SSH key pairs
- Enter SSH keys manually
- Set up both staging and production keys

## Security Group Setup

### Finding Security Group IDs

1. **AWS Console Method**:
   - Go to EC2 Console → Security Groups
   - Find the security groups for your staging/production instances
   - Copy the Group ID (format: `sg-xxxxxxxxx`)

2. **AWS CLI Method**:
   ```bash
   # List all security groups
   aws ec2 describe-security-groups --query 'SecurityGroups[*].[GroupId,GroupName,Description]' --output table
   
   # Find by instance ID
   aws ec2 describe-instances --instance-ids i-1234567890abcdef0 --query 'Reservations[*].Instances[*].SecurityGroups[*].[GroupId,GroupName]' --output table
   ```

### Security Group Requirements

The security groups should:
- **NOT** have permanent SSH (port 22) rules from `0.0.0.0/0`
- Allow SSH from your current IP for initial setup
- Allow application ports (3001 for prod, 3002 for staging) as needed
- Allow Docker registry access if running on the same instances

## GitHub Secrets Created

The setup script creates these secrets:

### AWS Configuration
- `AWS_ACCESS_KEY_ID` - IAM user access key
- `AWS_SECRET_ACCESS_KEY` - IAM user secret key
- `AWS_REGION` - AWS region for resources

### Security Groups
- `STAGING_SECURITY_GROUP_ID` - Security group for staging EC2
- `PROD_SECURITY_GROUP_ID` - Security group for production EC2

### Docker Registry
- `DOCKER_REGISTRY` - Registry URL (e.g., `registry.example.com:5000`)
- `DOCKER_HTTP_SECRET` - HTTP secret for insecure registries

### SSH Configuration
- `STAGING_HOST` - Staging server IP/hostname
- `PROD_HOST` - Production server IP/hostname
- `STAGING_USERNAME` - SSH username for staging
- `PROD_USERNAME` - SSH username for production
- `STAGING_SSH_PORT` - SSH port for staging (default: 22)
- `PROD_SSH_PORT` - SSH port for production (default: 22)
- `STAGING_SSH_KEY` - SSH private key for staging
- `PROD_SSH_KEY` - SSH private key for production

### Application Paths
- `STAGING_APP_PATH` - Application directory on staging server
- `PROD_APP_PATH` - Application directory on production server

## Additional Secrets (Manual Setup)

You may need to manually set these secrets based on your application requirements:

### Database Configuration
```bash
# Staging database
gh secret set STAGING_DB_HOST --repo ConservationInternational/trends.earth-API
gh secret set STAGING_DB_PORT --repo ConservationInternational/trends.earth-API
gh secret set STAGING_DB_NAME --repo ConservationInternational/trends.earth-API
gh secret set STAGING_DB_USER --repo ConservationInternational/trends.earth-API
gh secret set STAGING_DB_PASSWORD --repo ConservationInternational/trends.earth-API

# Production database
gh secret set PROD_DB_HOST --repo ConservationInternational/trends.earth-API
gh secret set PROD_DB_PORT --repo ConservationInternational/trends.earth-API
gh secret set PROD_DB_NAME --repo ConservationInternational/trends.earth-API
gh secret set PROD_DB_USER --repo ConservationInternational/trends.earth-API
gh secret set PROD_DB_PASSWORD --repo ConservationInternational/trends.earth-API
```

### Test User Credentials
```bash
# Test users for integration tests
gh secret set TEST_SUPERADMIN_EMAIL --repo ConservationInternational/trends.earth-API
gh secret set TEST_ADMIN_EMAIL --repo ConservationInternational/trends.earth-API
gh secret set TEST_USER_EMAIL --repo ConservationInternational/trends.earth-API
gh secret set TEST_SUPERADMIN_PASSWORD --repo ConservationInternational/trends.earth-API
gh secret set TEST_ADMIN_PASSWORD --repo ConservationInternational/trends.earth-API
gh secret set TEST_USER_PASSWORD --repo ConservationInternational/trends.earth-API
```

## IAM Permissions Created

The setup creates an IAM user with this minimal policy:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ManageSecurityGroupIngress",
            "Effect": "Allow",
            "Action": [
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:DescribeSecurityGroups"
            ],
            "Resource": [
                "arn:aws:ec2:REGION:*:security-group/STAGING_SG_ID",
                "arn:aws:ec2:REGION:*:security-group/PROD_SG_ID"
            ]
        },
        {
            "Sid": "DescribeSecurityGroups",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeSecurityGroups"
            ],
            "Resource": "*"
        }
    ]
}
```

## Troubleshooting

### Common Issues

1. **"Access Denied" errors**:
   - Ensure your AWS credentials have sufficient permissions
   - Check that the IAM user has the correct policy attached

2. **SSH connection failures**:
   - Verify the SSH key is correctly formatted
   - Ensure the public key is added to `~/.ssh/authorized_keys` on the server
   - Check that the security group allows SSH from your IP

3. **Docker registry access issues**:
   - Verify the registry URL is correct
   - Check that the Docker HTTP secret is properly configured for insecure registries
   - Ensure the registry is accessible from the EC2 instances

### Verification Commands

```bash
# Test AWS credentials
aws sts get-caller-identity

# Test GitHub CLI authentication
gh auth status

# Test SSH connection
ssh -i ~/.ssh/your-key user@your-host

# Check GitHub secrets
gh secret list --repo ConservationInternational/trends.earth-API

# Test security group modification (replace with your values)
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp \
  --port 22 \
  --cidr 1.2.3.4/32 \
  --description "Test rule"

aws ec2 revoke-security-group-ingress \
  --group-id sg-xxxxxxxxx \
  --protocol tcp \
  --port 22 \
  --cidr 1.2.3.4/32
```

## Security Considerations

1. **Least Privilege**: IAM user only has permissions for specific security groups
2. **Temporary Access**: SSH rules are automatically removed after deployment
3. **IP Restriction**: Only the specific GitHub Actions runner IP gets access
4. **No Permanent Keys**: Access keys are created fresh and can be rotated
5. **Audit Trail**: All security group changes include timestamps

## Cleanup

To remove the setup:

```bash
# Delete IAM user (this will also remove access keys)
aws iam detach-user-policy --user-name github-actions-deployment --policy-arn arn:aws:iam::ACCOUNT:policy/GitHubActionsSecurityGroupPolicy
aws iam delete-user --user-name github-actions-deployment

# Delete IAM policy
aws iam delete-policy --policy-arn arn:aws:iam::ACCOUNT:policy/GitHubActionsSecurityGroupPolicy

# Remove GitHub secrets (optional)
gh secret delete AWS_ACCESS_KEY_ID --repo ConservationInternational/trends.earth-API
gh secret delete AWS_SECRET_ACCESS_KEY --repo ConservationInternational/trends.earth-API
# ... (repeat for other secrets)
```

## Swagger UI Asset Management

### Download Swagger UI Assets

Use the `download_swagger_ui.py` script to download and host Swagger UI assets locally:

```bash
# Download latest Swagger UI assets
python3 scripts/download_swagger_ui.py
```

**Benefits of Local Hosting:**
- Enhanced security (no external CDN dependencies)
- Better performance (faster loading)
- Simplified Content Security Policy
- Offline documentation capability

**Assets Location:** `gefapi/static/swagger-ui/`

**Version Updates:** Edit the `SWAGGER_VERSION` variable in the script when upgrading.

## Validation and Troubleshooting

### Validate Staging Environment Setup

Use the staging validation script to check if all required GitHub secrets are configured for script import:

```bash
# Check if staging can import scripts from production
chmod +x scripts/validate-staging-secrets.sh
./scripts/validate-staging-secrets.sh
```

**What it checks:**
- Production database connection secrets (for data import)
- Test user credential secrets (for staging users)
- GitHub CLI authentication and access

**Use cases:**
- Troubleshooting missing scripts/logs in staging
- Verifying setup after running `setup-github-deployment.sh`
- Debugging staging deployment issues

### Common Issues

**Scripts not appearing in staging:**
1. Run `./scripts/validate-staging-secrets.sh` to check secrets
2. Check migrate service logs: `docker service logs trends-earth-staging_migrate`
3. Verify production database connectivity from staging server

**Authentication failures:**
1. Verify test user secrets are set correctly
2. Check password format (no special characters that break shell)
3. Ensure test users were created by checking migrate service logs
