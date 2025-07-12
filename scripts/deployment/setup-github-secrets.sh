#!/bin/bash
# GitHub Secrets Setup Script for Trends.Earth API Deployment
# This script helps configure GitHub repository secrets for CI/CD deployment

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# Function to check if GitHub CLI is installed
check_gh_cli() {
    if ! command -v gh &> /dev/null; then
        print_error "GitHub CLI is not installed."
        print_error "Please install it from: https://cli.github.com/"
        print_error "Or install via package manager:"
        print_error "  Ubuntu/Debian: apt install gh"
        print_error "  macOS: brew install gh"
        exit 1
    fi
    
    if ! gh auth status &> /dev/null; then
        print_error "GitHub CLI is not authenticated."
        print_error "Please run: gh auth login"
        exit 1
    fi
    
    print_success "GitHub CLI is installed and authenticated"
}

# Function to generate SSH key pair
generate_ssh_key() {
    local env_type=$1
    local key_path="$HOME/.ssh/trends_earth_${env_type}_deploy"
    
    if [[ -f "$key_path" ]]; then
        print_warning "SSH key already exists: $key_path"
        read -p "Generate new SSH key? (y/n): " generate_new
        if [[ ! "$generate_new" =~ ^[Yy]$ ]]; then
            return 0
        fi
    fi
    
    print_status "Generating SSH key pair for $env_type deployment..."
    
    ssh-keygen -t ed25519 -f "$key_path" -N "" -C "trends-earth-${env_type}-deploy"
    
    print_success "SSH key generated: $key_path"
    print_status "Public key content:"
    echo "----------------------------------------"
    cat "${key_path}.pub"
    echo "----------------------------------------"
    print_warning "Add the above public key to the authorized_keys file on your $env_type server"
    print_warning "Command: ssh-copy-id -i ${key_path}.pub user@your-${env_type}-server"
}

# Function to set GitHub secret
set_github_secret() {
    local secret_name=$1
    local secret_value=$2
    local env_type=$3
    
    if [[ -n "$env_type" ]]; then
        # Set environment-specific secret
        echo "$secret_value" | gh secret set "$secret_name" --env "$env_type"
        print_success "Set $env_type environment secret: $secret_name"
    else
        # Set repository-wide secret
        echo "$secret_value" | gh secret set "$secret_name"
        print_success "Set repository secret: $secret_name"
    fi
}

# Function to setup production secrets
setup_production_secrets() {
    print_status "Setting up production deployment secrets..."
    
    # Server connection details
    read -p "Enter production server hostname/IP: " PROD_HOST
    read -p "Enter production server SSH username: " PROD_USERNAME
    read -p "Enter production server SSH port (default: 22): " PROD_SSH_PORT
    PROD_SSH_PORT=${PROD_SSH_PORT:-22}
    read -p "Enter production app directory path (default: /opt/trends-earth-api): " PROD_APP_PATH
    PROD_APP_PATH=${PROD_APP_PATH:-/opt/trends-earth-api}
    
    # Generate SSH key
    generate_ssh_key "production"
    local ssh_key_path="$HOME/.ssh/trends_earth_production_deploy"
    
    # Set secrets
    set_github_secret "PROD_HOST" "$PROD_HOST" "production"
    set_github_secret "PROD_USERNAME" "$PROD_USERNAME" "production"
    set_github_secret "PROD_SSH_PORT" "$PROD_SSH_PORT" "production"
    set_github_secret "PROD_APP_PATH" "$PROD_APP_PATH" "production"
    set_github_secret "PROD_SSH_KEY" "$(cat $ssh_key_path)" "production"
    
    print_success "Production secrets configured"
}

# Function to setup staging secrets
setup_staging_secrets() {
    print_status "Setting up staging deployment secrets..."
    
    # Server connection details
    read -p "Enter staging server hostname/IP: " STAGING_HOST
    read -p "Enter staging server SSH username: " STAGING_USERNAME
    read -p "Enter staging server SSH port (default: 22): " STAGING_SSH_PORT
    STAGING_SSH_PORT=${STAGING_SSH_PORT:-22}
    read -p "Enter staging app directory path (default: /opt/trends-earth-api-staging): " STAGING_APP_PATH
    STAGING_APP_PATH=${STAGING_APP_PATH:-/opt/trends-earth-api-staging}
    
    # Generate SSH key
    generate_ssh_key "staging"
    local ssh_key_path="$HOME/.ssh/trends_earth_staging_deploy"
    
    # Set secrets
    set_github_secret "STAGING_HOST" "$STAGING_HOST" "staging"
    set_github_secret "STAGING_USERNAME" "$STAGING_USERNAME" "staging"
    set_github_secret "STAGING_SSH_PORT" "$STAGING_SSH_PORT" "staging"
    set_github_secret "STAGING_APP_PATH" "$STAGING_APP_PATH" "staging"
    set_github_secret "STAGING_SSH_KEY" "$(cat $ssh_key_path)" "staging"
    
    print_success "Staging secrets configured"
}

# Function to setup Docker registry secrets
setup_docker_secrets() {
    print_status "Setting up Docker registry secrets..."
    
    read -p "Enter Docker registry URL (e.g., registry.company.com:5000): " DOCKER_REGISTRY
    read -p "Enter Docker registry username: " DOCKER_USERNAME
    read -s -p "Enter Docker registry password: " DOCKER_PASSWORD
    echo
    
    # Set repository-wide secrets (used by both environments)
    set_github_secret "DOCKER_REGISTRY" "$DOCKER_REGISTRY"
    set_github_secret "DOCKER_USERNAME" "$DOCKER_USERNAME"
    set_github_secret "DOCKER_PASSWORD" "$DOCKER_PASSWORD"
    
    print_success "Docker registry secrets configured"
}

# Function to setup optional notification secrets
setup_notification_secrets() {
    print_status "Setting up optional notification secrets..."
    
    read -p "Enter Slack webhook URL (optional, press enter to skip): " SLACK_WEBHOOK_URL
    if [[ -n "$SLACK_WEBHOOK_URL" ]]; then
        set_github_secret "SLACK_WEBHOOK_URL" "$SLACK_WEBHOOK_URL"
        print_success "Slack webhook configured"
    fi
}

# Function to create GitHub environments
create_github_environments() {
    print_status "Creating GitHub environments..."
    
    # Create production environment
    gh api -X PUT "/repos/:owner/:repo/environments/production" \
        --field wait_timer=0 \
        --field prevent_self_review=false \
        --field reviewers='[]' || print_warning "Could not create production environment"
    
    # Create staging environment
    gh api -X PUT "/repos/:owner/:repo/environments/staging" \
        --field wait_timer=0 \
        --field prevent_self_review=false \
        --field reviewers='[]' || print_warning "Could not create staging environment"
    
    print_success "GitHub environments created"
}

# Function to list current secrets
list_secrets() {
    print_status "Current repository secrets:"
    gh secret list
    
    print_status "Production environment secrets:"
    gh secret list --env production 2>/dev/null || print_warning "No production environment found"
    
    print_status "Staging environment secrets:"
    gh secret list --env staging 2>/dev/null || print_warning "No staging environment found"
}

# Main execution
main() {
    print_status "GitHub Secrets Setup for Trends.Earth API Deployment"
    print_status "====================================================="
    
    check_gh_cli
    
    # Create environments first
    create_github_environments
    
    # Setup Docker registry secrets (shared)
    setup_docker_secrets
    
    # Setup production secrets
    read -p "Setup production deployment secrets? (y/n): " setup_prod
    if [[ "$setup_prod" =~ ^[Yy]$ ]]; then
        setup_production_secrets
    fi
    
    # Setup staging secrets
    read -p "Setup staging deployment secrets? (y/n): " setup_staging
    if [[ "$setup_staging" =~ ^[Yy]$ ]]; then
        setup_staging_secrets
    fi
    
    # Setup notification secrets
    read -p "Setup notification secrets? (y/n): " setup_notifications
    if [[ "$setup_notifications" =~ ^[Yy]$ ]]; then
        setup_notification_secrets
    fi
    
    print_success "GitHub secrets setup completed!"
    print_status "====================================================="
    
    # List all secrets
    list_secrets
    
    print_status "Next steps:"
    print_status "1. Verify SSH access to your servers with the generated keys"
    print_status "2. Test deployment workflows in GitHub Actions"
    print_status "3. Monitor deployment logs in GitHub Actions tab"
}

# Run main function
main "$@"
