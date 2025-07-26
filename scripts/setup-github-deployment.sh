#!/bin/bash

# Setup script for GitHub Actions deployment with dynamic security group management
# This script creates an IAM user with EC2 security group permissions and sets GitHub environment secrets
#
# Usage:
#   ./setup-github-deployment.sh         # Start fresh setup
#   ./setup-github-deployment.sh --recover # Resume from saved configuration

set -e

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
source "$SCRIPT_DIR/common.sh"

# Deployment-specific configuration
IAM_USER_NAME="github-actions-deployment"
POLICY_NAME="GitHubActionsSecurityGroupPolicy"

# Function to check if required tools are installed
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    check_aws_cli
    check_github_cli
    
    print_success "All prerequisites are met"
}

# Function to create GitHub environments if they don't exist
create_github_environments() {
    print_status "Ensuring GitHub environments exist..."
    
    # Check and create staging environment
    if ! gh api "repos/$GITHUB_OWNER/$GITHUB_REPO/environments/staging" &> /dev/null; then
        print_status "Creating staging environment..."
        if gh api --method PUT "repos/$GITHUB_OWNER/$GITHUB_REPO/environments/staging" \
            --field wait_timer=0 \
            --field prevent_self_review=false \
            --field reviewers='[]' \
            --field deployment_branch_policy='{"protected_branches":false,"custom_branch_policies":true}' &> /dev/null; then
            print_success "Staging environment created successfully"
        else
            print_warning "Could not create staging environment via API"
            print_warning "Please create it manually:"
            echo "  1. Go to: https://github.com/$GITHUB_OWNER/$GITHUB_REPO/settings/environments"
            echo "  2. Click 'New environment'"
            echo "  3. Name it 'staging'"
            echo "  4. Click 'Configure environment'"
            echo "  5. Disable protection rules for now and save"
            echo
            read -p "Press Enter after creating the staging environment manually..."
        fi
    else
        print_success "Staging environment already exists"
    fi
    
    # Check and create production environment
    if ! gh api "repos/$GITHUB_OWNER/$GITHUB_REPO/environments/production" &> /dev/null; then
        print_status "Creating production environment..."
        if gh api --method PUT "repos/$GITHUB_OWNER/$GITHUB_REPO/environments/production" \
            --field wait_timer=0 \
            --field prevent_self_review=false \
            --field reviewers='[]' \
            --field deployment_branch_policy='{"protected_branches":false,"custom_branch_policies":true}' &> /dev/null; then
            print_success "Production environment created successfully"
        else
            print_warning "Could not create production environment via API"
            print_warning "Please create it manually:"
            echo "  1. Go to: https://github.com/$GITHUB_OWNER/$GITHUB_REPO/settings/environments"
            echo "  2. Click 'New environment'"
            echo "  3. Name it 'production'"
            echo "  4. Click 'Configure environment'"
            echo "  5. You may want to add protection rules for production"
            echo
            read -p "Press Enter after creating the production environment manually..."
        fi
    else
        print_success "Production environment already exists"
    fi
}

# Function to save configuration to file for recovery
save_config() {
    local config_file="/tmp/github-deployment-config.env"
    cat > "$config_file" << EOF
# GitHub Deployment Configuration
# Generated: $(date)
AWS_REGION="$AWS_REGION"
STAGING_SG_ID="$STAGING_SG_ID"
PROD_SG_ID="$PROD_SG_ID"
DOCKER_REGISTRY="$DOCKER_REGISTRY"
DOCKER_HTTP_SECRET="$DOCKER_HTTP_SECRET"
STAGING_HOST="$STAGING_HOST"
PROD_HOST="$PROD_HOST"
SSH_USERNAME="$SSH_USERNAME"
SSH_PORT="$SSH_PORT"
STAGING_APP_PATH="$STAGING_APP_PATH"
PROD_APP_PATH="$PROD_APP_PATH"
STAGING_DB_HOST="$STAGING_DB_HOST"
STAGING_DB_PORT="$STAGING_DB_PORT"
STAGING_DB_NAME="$STAGING_DB_NAME"
STAGING_DB_USER="$STAGING_DB_USER"
STAGING_DB_PASSWORD="$STAGING_DB_PASSWORD"
PROD_DB_HOST="$PROD_DB_HOST"
PROD_DB_PORT="$PROD_DB_PORT"
PROD_DB_NAME="$PROD_DB_NAME"
PROD_DB_USER="$PROD_DB_USER"
PROD_DB_PASSWORD="$PROD_DB_PASSWORD"
TEST_SUPERADMIN_EMAIL="$TEST_SUPERADMIN_EMAIL"
TEST_ADMIN_EMAIL="$TEST_ADMIN_EMAIL"
TEST_USER_EMAIL="$TEST_USER_EMAIL"
TEST_SUPERADMIN_PASSWORD="$TEST_SUPERADMIN_PASSWORD"
TEST_ADMIN_PASSWORD="$TEST_ADMIN_PASSWORD"
TEST_USER_PASSWORD="$TEST_USER_PASSWORD"
EOF
    print_success "Configuration saved to: $config_file"
}

# Function to load configuration from file
load_config() {
    local config_file="/tmp/github-deployment-config.env"
    if [[ -f "$config_file" ]]; then
        print_status "Loading previous configuration from: $config_file"
        source "$config_file"
        print_success "Configuration loaded successfully"
        return 0
    else
        return 1
    fi
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
    
    # Database configuration
    echo
    print_status "Database configuration..."
    echo -n "Enter staging database host (default: localhost): "
    read -r STAGING_DB_HOST
    STAGING_DB_HOST=${STAGING_DB_HOST:-localhost}
    
    echo -n "Enter staging database port (default: 5433): "
    read -r STAGING_DB_PORT
    STAGING_DB_PORT=${STAGING_DB_PORT:-5433}
    
    echo -n "Enter staging database name (default: trendsearth_staging): "
    read -r STAGING_DB_NAME
    STAGING_DB_NAME=${STAGING_DB_NAME:-trendsearth_staging}
    
    echo -n "Enter staging database username (default: trendsearth_staging): "
    read -r STAGING_DB_USER
    STAGING_DB_USER=${STAGING_DB_USER:-trendsearth_staging}
    
    echo -n "Enter staging database password: "
    read -s STAGING_DB_PASSWORD
    echo
    
    echo -n "Enter production database host: "
    read -r PROD_DB_HOST
    
    echo -n "Enter production database port (default: 5432): "
    read -r PROD_DB_PORT
    PROD_DB_PORT=${PROD_DB_PORT:-5432}
    
    echo -n "Enter production database name (default: trendsearth): "
    read -r PROD_DB_NAME
    PROD_DB_NAME=${PROD_DB_NAME:-trendsearth}
    
    echo -n "Enter production database username: "
    read -r PROD_DB_USER
    
    echo -n "Enter production database password: "
    read -s PROD_DB_PASSWORD
    echo
    
    # Test user configuration (for staging testing)
    echo
    print_status "Test user configuration (for staging environment testing)..."
    echo -n "Enter test superadmin email (default: superadmin@test.example.com): "
    read -r TEST_SUPERADMIN_EMAIL
    TEST_SUPERADMIN_EMAIL=${TEST_SUPERADMIN_EMAIL:-superadmin@test.example.com}
    
    echo -n "Enter test admin email (default: admin@test.example.com): "
    read -r TEST_ADMIN_EMAIL
    TEST_ADMIN_EMAIL=${TEST_ADMIN_EMAIL:-admin@test.example.com}
    
    echo -n "Enter test user email (default: user@test.example.com): "
    read -r TEST_USER_EMAIL
    TEST_USER_EMAIL=${TEST_USER_EMAIL:-user@test.example.com}
    
    echo -n "Enter test superadmin password (default: TestPass123!): "
    read -s TEST_SUPERADMIN_PASSWORD
    TEST_SUPERADMIN_PASSWORD=${TEST_SUPERADMIN_PASSWORD:-TestPass123!}
    echo
    
    echo -n "Enter test admin password (default: TestPass123!): "
    read -s TEST_ADMIN_PASSWORD
    TEST_ADMIN_PASSWORD=${TEST_ADMIN_PASSWORD:-TestPass123!}
    echo
    
    echo -n "Enter test user password (default: TestPass123!): "
    read -s TEST_USER_PASSWORD
    TEST_USER_PASSWORD=${TEST_USER_PASSWORD:-TestPass123!}
    echo
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
    print_status "Setting GitHub environment secrets..."
    
    # Helper function to set a secret with error handling
    set_secret_safe() {
        local secret_name="$1"
        local secret_value="$2"
        local environment="$3"
        
        if [[ -n "$environment" ]]; then
            if ! gh secret set "$secret_name" --body "$secret_value" --repo "$GITHUB_OWNER/$GITHUB_REPO" --env "$environment" 2>/dev/null; then
                print_error "Failed to set $secret_name in $environment environment"
                print_warning "Run: ./setup-github-deployment.sh --recover"
                print_warning "Or create the $environment environment manually in GitHub and retry"
                return 1
            fi
        else
            if ! gh secret set "$secret_name" --body "$secret_value" --repo "$GITHUB_OWNER/$GITHUB_REPO" 2>/dev/null; then
                print_error "Failed to set repository secret $secret_name"
                return 1
            fi
        fi
    }
    
    # Set shared AWS credentials (repository level - used by both environments)
    print_status "Setting repository-level secrets..."
    set_secret_safe "AWS_ACCESS_KEY_ID" "$AWS_ACCESS_KEY_ID" || return 1
    set_secret_safe "AWS_SECRET_ACCESS_KEY" "$AWS_SECRET_ACCESS_KEY" || return 1
    set_secret_safe "AWS_REGION" "$AWS_REGION" || return 1
    
    # Set shared Docker registry configuration (repository level)
    set_secret_safe "DOCKER_REGISTRY" "$DOCKER_REGISTRY" || return 1
    set_secret_safe "DOCKER_HTTP_SECRET" "$DOCKER_HTTP_SECRET" || return 1
    
    # Set staging environment secrets
    print_status "Setting staging environment secrets..."
    set_secret_safe "STAGING_SECURITY_GROUP_ID" "$STAGING_SG_ID" "staging" || return 1
    set_secret_safe "STAGING_HOST" "$STAGING_HOST" "staging" || return 1
    set_secret_safe "STAGING_USERNAME" "$SSH_USERNAME" "staging" || return 1
    set_secret_safe "STAGING_SSH_PORT" "$SSH_PORT" "staging" || return 1
    set_secret_safe "STAGING_APP_PATH" "$STAGING_APP_PATH" "staging" || return 1
    
    # Set staging database secrets
    set_secret_safe "STAGING_DB_HOST" "$STAGING_DB_HOST" "staging" || return 1
    set_secret_safe "STAGING_DB_PORT" "$STAGING_DB_PORT" "staging" || return 1
    set_secret_safe "STAGING_DB_NAME" "$STAGING_DB_NAME" "staging" || return 1
    set_secret_safe "STAGING_DB_USER" "$STAGING_DB_USER" "staging" || return 1
    set_secret_safe "STAGING_DB_PASSWORD" "$STAGING_DB_PASSWORD" "staging" || return 1
    
    # Set test user secrets (staging environment only)
    set_secret_safe "TEST_SUPERADMIN_EMAIL" "$TEST_SUPERADMIN_EMAIL" "staging" || return 1
    set_secret_safe "TEST_ADMIN_EMAIL" "$TEST_ADMIN_EMAIL" "staging" || return 1
    set_secret_safe "TEST_USER_EMAIL" "$TEST_USER_EMAIL" "staging" || return 1
    set_secret_safe "TEST_SUPERADMIN_PASSWORD" "$TEST_SUPERADMIN_PASSWORD" "staging" || return 1
    set_secret_safe "TEST_ADMIN_PASSWORD" "$TEST_ADMIN_PASSWORD" "staging" || return 1
    set_secret_safe "TEST_USER_PASSWORD" "$TEST_USER_PASSWORD" "staging" || return 1
    
    # Set production environment secrets
    print_status "Setting production environment secrets..."
    set_secret_safe "PROD_SECURITY_GROUP_ID" "$PROD_SG_ID" "production" || return 1
    set_secret_safe "PROD_HOST" "$PROD_HOST" "production" || return 1
    set_secret_safe "PROD_USERNAME" "$SSH_USERNAME" "production" || return 1
    set_secret_safe "PROD_SSH_PORT" "$SSH_PORT" "production" || return 1
    set_secret_safe "PROD_APP_PATH" "$PROD_APP_PATH" "production" || return 1
    
    # Set production database secrets
    set_secret_safe "PROD_DB_HOST" "$PROD_DB_HOST" "production" || return 1
    set_secret_safe "PROD_DB_PORT" "$PROD_DB_PORT" "production" || return 1
    set_secret_safe "PROD_DB_NAME" "$PROD_DB_NAME" "production" || return 1
    set_secret_safe "PROD_DB_USER" "$PROD_DB_USER" "production" || return 1
    set_secret_safe "PROD_DB_PASSWORD" "$PROD_DB_PASSWORD" "production" || return 1
    
    # IMPORTANT: Also set production DB secrets in staging environment
    # This is needed because staging workflow imports data from production
    print_status "Setting production database access for staging environment..."
    set_secret_safe "PROD_DB_HOST" "$PROD_DB_HOST" "staging" || return 1
    set_secret_safe "PROD_DB_PORT" "$PROD_DB_PORT" "staging" || return 1
    set_secret_safe "PROD_DB_NAME" "$PROD_DB_NAME" "staging" || return 1
    set_secret_safe "PROD_DB_USER" "$PROD_DB_USER" "staging" || return 1
    set_secret_safe "PROD_DB_PASSWORD" "$PROD_DB_PASSWORD" "staging" || return 1
    
    print_success "All GitHub environment secrets have been set"
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
    echo "1. Set SSH private keys using environment secrets:"
    echo "   Staging: STAGING_SSH_KEY (in staging environment)"
    echo "   Production: PROD_SSH_KEY (in production environment)"
    echo "   Run: ./scripts/setup-ssh-keys.sh or ./scripts/fix-ssh-auth.sh"
    echo
    echo "2. All database and test user secrets have been configured:"
    echo "   ✅ Staging environment: STAGING_DB_* secrets, TEST_* secrets"
    echo "   ✅ Production environment: PROD_DB_* secrets"
    echo "   ✅ Cross-environment: PROD_DB_* also set in staging (for data import)"
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
    
    # Check for recovery mode
    if [[ "$1" == "--recover" ]]; then
        print_status "Recovery mode: attempting to continue from previous setup..."
        if load_config; then
            print_status "Previous configuration loaded successfully"
        else
            print_error "No saved configuration found for recovery"
            echo "Run without --recover to start fresh setup"
            exit 1
        fi
    else
        echo "This script will:"
        echo "1. Create an IAM user with security group management permissions"
        echo "2. Generate access keys for the IAM user"
        echo "3. Set all necessary GitHub repository and environment secrets including:"
        echo "   - AWS credentials and Docker registry (repository level)"
        echo "   - SSH connection details (environment level)"
        echo "   - Database configurations (environment level)"
        echo "   - Test user credentials (staging environment)"
        echo
        
        read -p "Do you want to continue? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_warning "Setup cancelled"
            exit 0
        fi
        
        check_prerequisites
        get_user_input
        save_config
    fi
    
    # Ensure GitHub environments exist before setting secrets
    create_github_environments
    
    create_iam_policy
    create_iam_user
    create_access_keys
    
    # Try to set GitHub secrets with error handling
    if ! set_github_secrets; then
        print_error "Failed to set some GitHub secrets"
        print_warning "Your configuration has been saved for recovery"
        print_warning "To retry: ./setup-github-deployment.sh --recover"
        exit 1
    fi
    
    display_summary
    
    # Clean up config file after successful completion
    if [[ "$1" != "--recover" ]]; then
        rm -f "/tmp/github-deployment-config.env" || true
        print_success "Temporary configuration file cleaned up"
    fi
}

# Run main function
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    echo "GitHub Actions Deployment Setup"
    echo
    echo "This script creates AWS IAM resources and sets GitHub secrets for automated deployment."
    echo
    echo "Usage:"
    echo "  $0              Start fresh setup (interactive)"
    echo "  $0 --recover    Resume from saved configuration after error"
    echo "  $0 --help       Show this help message"
    echo
    echo "Requirements:"
    echo "  - AWS CLI configured with appropriate permissions"
    echo "  - GitHub CLI (gh) authenticated"
    echo "  - Repository write access"
    echo
    echo "The script will:"
    echo "  1. Create IAM user with security group management permissions"
    echo "  2. Generate AWS access keys"
    echo "  3. Create GitHub environments (staging, production)"
    echo "  4. Set all required repository and environment secrets"
    echo
    exit 0
else
    main "$@"
fi
