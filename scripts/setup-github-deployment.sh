#!/bin/bash

# Setup script for GitHub Actions deployment with dynamic security group management
# This script creates an IAM user with EC2 security group permissions and sets GitHub secrets

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration - Update these values for your setup
GITHUB_OWNER="ConservationInternational"
GITHUB_REPO="trends.earth-API"
IAM_USER_NAME="github-actions-deployment"
POLICY_NAME="GitHubActionsSecurityGroupPolicy"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if required tools are installed
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        print_error "AWS CLI is not installed. Please install it first."
        exit 1
    fi
    
    # Check GitHub CLI
    if ! command -v gh &> /dev/null; then
        print_error "GitHub CLI is not installed. Please install it first."
        exit 1
    fi
    
    # Check if authenticated with AWS
    if ! aws sts get-caller-identity &> /dev/null; then
        print_error "Not authenticated with AWS. Please run 'aws configure' first."
        exit 1
    fi
    
    # Check if authenticated with GitHub
    if ! gh auth status &> /dev/null; then
        print_error "Not authenticated with GitHub CLI. Please run 'gh auth login' first."
        exit 1
    fi
    
    print_success "All prerequisites are met"
}

# Function to get user input for required values
get_user_input() {
    print_status "Gathering configuration information..."
    
    # Get AWS region
    echo -n "Enter AWS region (default: us-east-1): "
    read -r AWS_REGION
    AWS_REGION=${AWS_REGION:-us-east-1}
    
    # Get staging security group ID
    echo -n "Enter staging security group ID: "
    read -r STAGING_SG_ID
    if [[ -z "$STAGING_SG_ID" ]]; then
        print_error "Staging security group ID is required"
        exit 1
    fi
    
    # Get production security group ID
    echo -n "Enter production security group ID: "
    read -r PROD_SG_ID
    if [[ -z "$PROD_SG_ID" ]]; then
        print_error "Production security group ID is required"
        exit 1
    fi
    
    # Get Docker registry URL
    echo -n "Enter Docker registry URL (e.g., registry.example.com:5000): "
    read -r DOCKER_REGISTRY
    if [[ -z "$DOCKER_REGISTRY" ]]; then
        print_error "Docker registry URL is required"
        exit 1
    fi
    
    # Get Docker registry HTTP secret for insecure registries
    echo -n "Enter Docker registry HTTP secret (for insecure registries): "
    read -r DOCKER_HTTP_SECRET
    if [[ -z "$DOCKER_HTTP_SECRET" ]]; then
        print_error "Docker registry HTTP secret is required"
        exit 1
    fi
    
    # SSH configuration
    echo -n "Enter staging host IP/hostname: "
    read -r STAGING_HOST
    if [[ -z "$STAGING_HOST" ]]; then
        print_error "Staging host is required"
        exit 1
    fi
    
    echo -n "Enter production host IP/hostname: "
    read -r PROD_HOST
    if [[ -z "$PROD_HOST" ]]; then
        print_error "Production host is required"
        exit 1
    fi
    
    echo -n "Enter SSH username (default: ubuntu): "
    read -r SSH_USERNAME
    SSH_USERNAME=${SSH_USERNAME:-ubuntu}
    
    echo -n "Enter SSH port (default: 22): "
    read -r SSH_PORT
    SSH_PORT=${SSH_PORT:-22}
    
    # Application paths
    echo -n "Enter staging application path (default: /opt/trends-earth-api-staging): "
    read -r STAGING_APP_PATH
    STAGING_APP_PATH=${STAGING_APP_PATH:-/opt/trends-earth-api-staging}
    
    echo -n "Enter production application path (default: /opt/trends-earth-api): "
    read -r PROD_APP_PATH
    PROD_APP_PATH=${PROD_APP_PATH:-/opt/trends-earth-api}
}

# Function to create IAM policy
create_iam_policy() {
    print_status "Creating IAM policy for security group management..."
    
    # Create policy document
    cat > /tmp/security-group-policy.json << EOF
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
                "arn:aws:ec2:${AWS_REGION}:*:security-group/${STAGING_SG_ID}",
                "arn:aws:ec2:${AWS_REGION}:*:security-group/${PROD_SG_ID}"
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
EOF
    
    # Create or update the policy
    POLICY_ARN="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/${POLICY_NAME}"
    
    if aws iam get-policy --policy-arn "$POLICY_ARN" &> /dev/null; then
        print_warning "Policy already exists. Updating..."
        aws iam create-policy-version \
            --policy-arn "$POLICY_ARN" \
            --policy-document file:///tmp/security-group-policy.json \
            --set-as-default
    else
        aws iam create-policy \
            --policy-name "$POLICY_NAME" \
            --policy-document file:///tmp/security-group-policy.json \
            --description "Policy for GitHub Actions to manage security group rules for deployment"
    fi
    
    # Clean up temporary file
    rm -f /tmp/security-group-policy.json
    
    print_success "IAM policy created/updated: $POLICY_ARN"
}

# Function to create IAM user
create_iam_user() {
    print_status "Creating IAM user for GitHub Actions..."
    
    # Create user if it doesn't exist
    if aws iam get-user --user-name "$IAM_USER_NAME" &> /dev/null; then
        print_warning "IAM user already exists: $IAM_USER_NAME"
    else
        aws iam create-user \
            --user-name "$IAM_USER_NAME" \
            --tags Key=Purpose,Value=GitHubActions Key=Project,Value=TrendsEarth
        print_success "IAM user created: $IAM_USER_NAME"
    fi
    
    # Attach policy to user
    POLICY_ARN="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/${POLICY_NAME}"
    aws iam attach-user-policy \
        --user-name "$IAM_USER_NAME" \
        --policy-arn "$POLICY_ARN"
    
    print_success "Policy attached to user"
}

# Function to create access keys
create_access_keys() {
    print_status "Creating access keys for IAM user..."
    
    # Delete existing access keys (GitHub Actions only needs one)
    existing_keys=$(aws iam list-access-keys --user-name "$IAM_USER_NAME" --query 'AccessKeyMetadata[].AccessKeyId' --output text)
    for key in $existing_keys; do
        print_warning "Deleting existing access key: $key"
        aws iam delete-access-key --user-name "$IAM_USER_NAME" --access-key-id "$key"
    done
    
    # Create new access key
    key_output=$(aws iam create-access-key --user-name "$IAM_USER_NAME" --output json)
    AWS_ACCESS_KEY_ID=$(echo "$key_output" | jq -r '.AccessKey.AccessKeyId')
    AWS_SECRET_ACCESS_KEY=$(echo "$key_output" | jq -r '.AccessKey.SecretAccessKey')
    
    print_success "Access keys created for user: $IAM_USER_NAME"
}

# Function to set GitHub secrets
set_github_secrets() {
    print_status "Setting GitHub repository secrets..."
    
    # Set AWS credentials
    gh secret set AWS_ACCESS_KEY_ID --body "$AWS_ACCESS_KEY_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set AWS_SECRET_ACCESS_KEY --body "$AWS_SECRET_ACCESS_KEY" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set AWS_REGION --body "$AWS_REGION" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    
    # Set security group IDs
    gh secret set STAGING_SECURITY_GROUP_ID --body "$STAGING_SG_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set PROD_SECURITY_GROUP_ID --body "$PROD_SG_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    
    # Set Docker registry configuration
    gh secret set DOCKER_REGISTRY --body "$DOCKER_REGISTRY" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set DOCKER_HTTP_SECRET --body "$DOCKER_HTTP_SECRET" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    
    # Set SSH configuration
    gh secret set STAGING_HOST --body "$STAGING_HOST" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set PROD_HOST --body "$PROD_HOST" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set STAGING_USERNAME --body "$SSH_USERNAME" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set PROD_USERNAME --body "$SSH_USERNAME" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set STAGING_SSH_PORT --body "$SSH_PORT" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set PROD_SSH_PORT --body "$SSH_PORT" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    
    # Set application paths
    gh secret set STAGING_APP_PATH --body "$STAGING_APP_PATH" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    gh secret set PROD_APP_PATH --body "$PROD_APP_PATH" --repo "$GITHUB_OWNER/$GITHUB_REPO"
    
    print_success "All GitHub secrets have been set"
}

# Function to display summary
display_summary() {
    print_success "Setup completed successfully!"
    echo
    echo -e "${BLUE}=== SETUP SUMMARY ===${NC}"
    echo "IAM User: $IAM_USER_NAME"
    echo "IAM Policy: $POLICY_NAME"
    echo "AWS Region: $AWS_REGION"
    echo "Staging Security Group: $STAGING_SG_ID"
    echo "Production Security Group: $PROD_SG_ID"
    echo "GitHub Repository: $GITHUB_OWNER/$GITHUB_REPO"
    echo
    echo -e "${YELLOW}=== NEXT STEPS ===${NC}"
    echo "1. Ensure your SSH private keys are set as GitHub secrets:"
    echo "   - STAGING_SSH_KEY"
    echo "   - PROD_SSH_KEY"
    echo
    echo "2. Set up database and test user secrets if needed:"
    echo "   - STAGING_DB_* secrets"
    echo "   - PROD_DB_* secrets"
    echo "   - TEST_*_EMAIL and TEST_*_PASSWORD secrets"
    echo
    echo "3. Your GitHub Actions workflows are now ready to use dynamic security group management!"
    echo
    echo -e "${GREEN}✅ GitHub Actions can now automatically manage SSH access to your EC2 instances${NC}"
}

# Function to handle cleanup on script exit
cleanup() {
    if [[ -f /tmp/security-group-policy.json ]]; then
        rm -f /tmp/security-group-policy.json
    fi
}

# Set trap for cleanup
trap cleanup EXIT

# Main execution
main() {
    echo -e "${BLUE}=== GitHub Actions Deployment Setup ===${NC}"
    echo "This script will:"
    echo "1. Create an IAM user with security group management permissions"
    echo "2. Generate access keys for the IAM user"
    echo "3. Set all necessary GitHub repository secrets"
    echo
    
    read -p "Do you want to continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_warning "Setup cancelled"
        exit 0
    fi
    
    check_prerequisites
    get_user_input
    create_iam_policy
    create_iam_user
    create_access_keys
    set_github_secrets
    display_summary
}

# Run main function
main "$@"
