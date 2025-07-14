#!/bin/bash

# GitHub Secrets Validation Script
# This script checks that all required secrets are properly configured

set -e

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
source "$SCRIPT_DIR/common.sh"

# Function to check if a secret exists
check_secret() {
    local secret_name="$1"
    local environment="$2"
    local secret_type="$3"
    
    if [[ -n "$environment" ]]; then
        # Environment secret
        if gh secret list --repo "$GITHUB_OWNER/$GITHUB_REPO" --env "$environment" | grep -q "^$secret_name"; then
            print_success "✅ $secret_name ($secret_type - $environment environment)"
            return 0
        else
            print_error "❌ $secret_name ($secret_type - $environment environment) - MISSING"
            return 1
        fi
    else
        # Repository secret
        if gh secret list --repo "$GITHUB_OWNER/$GITHUB_REPO" | grep -q "^$secret_name"; then
            print_success "✅ $secret_name ($secret_type - repository level)"
            return 0
        else
            print_error "❌ $secret_name ($secret_type - repository level) - MISSING"
            return 1
        fi
    fi
}

# Function to validate all secrets
validate_secrets() {
    local missing_count=0
    
    print_status "Validating GitHub secrets configuration..."
    echo
    
    # Repository-level secrets
    print_status "Checking repository-level secrets (shared)..."
    check_secret "AWS_ACCESS_KEY_ID" "" "AWS credentials" || ((missing_count++))
    check_secret "AWS_SECRET_ACCESS_KEY" "" "AWS credentials" || ((missing_count++))
    check_secret "AWS_REGION" "" "AWS region" || ((missing_count++))
    check_secret "DOCKER_REGISTRY" "" "Docker registry" || ((missing_count++))
    check_secret "DOCKER_HTTP_SECRET" "" "Docker auth" || ((missing_count++))
    echo
    
    # Staging environment secrets
    print_status "Checking staging environment secrets..."
    check_secret "STAGING_SECURITY_GROUP_ID" "staging" "Security group" || ((missing_count++))
    check_secret "STAGING_HOST" "staging" "SSH connection" || ((missing_count++))
    check_secret "STAGING_USERNAME" "staging" "SSH connection" || ((missing_count++))
    check_secret "STAGING_SSH_KEY" "staging" "SSH key" || ((missing_count++))
    check_secret "STAGING_SSH_PORT" "staging" "SSH connection" || ((missing_count++))
    check_secret "STAGING_APP_PATH" "staging" "Application path" || ((missing_count++))
    check_secret "STAGING_DB_HOST" "staging" "Database" || ((missing_count++))
    check_secret "STAGING_DB_PORT" "staging" "Database" || ((missing_count++))
    check_secret "STAGING_DB_NAME" "staging" "Database" || ((missing_count++))
    check_secret "STAGING_DB_USER" "staging" "Database" || ((missing_count++))
    check_secret "STAGING_DB_PASSWORD" "staging" "Database" || ((missing_count++))
    check_secret "TEST_SUPERADMIN_EMAIL" "staging" "Test user" || ((missing_count++))
    check_secret "TEST_ADMIN_EMAIL" "staging" "Test user" || ((missing_count++))
    check_secret "TEST_USER_EMAIL" "staging" "Test user" || ((missing_count++))
    check_secret "TEST_SUPERADMIN_PASSWORD" "staging" "Test user" || ((missing_count++))
    check_secret "TEST_ADMIN_PASSWORD" "staging" "Test user" || ((missing_count++))
    check_secret "TEST_USER_PASSWORD" "staging" "Test user" || ((missing_count++))
    
    # Production DB secrets in staging (for data import)
    print_status "Checking production database access in staging environment..."
    check_secret "PROD_DB_HOST" "staging" "Prod DB access" || ((missing_count++))
    check_secret "PROD_DB_PORT" "staging" "Prod DB access" || ((missing_count++))
    check_secret "PROD_DB_NAME" "staging" "Prod DB access" || ((missing_count++))
    check_secret "PROD_DB_USER" "staging" "Prod DB access" || ((missing_count++))
    check_secret "PROD_DB_PASSWORD" "staging" "Prod DB access" || ((missing_count++))
    echo
    
    # Production environment secrets
    print_status "Checking production environment secrets..."
    check_secret "PROD_SECURITY_GROUP_ID" "production" "Security group" || ((missing_count++))
    check_secret "PROD_HOST" "production" "SSH connection" || ((missing_count++))
    check_secret "PROD_USERNAME" "production" "SSH connection" || ((missing_count++))
    check_secret "PROD_SSH_KEY" "production" "SSH key" || ((missing_count++))
    check_secret "PROD_SSH_PORT" "production" "SSH connection" || ((missing_count++))
    check_secret "PROD_APP_PATH" "production" "Application path" || ((missing_count++))
    check_secret "PROD_DB_HOST" "production" "Database" || ((missing_count++))
    check_secret "PROD_DB_PORT" "production" "Database" || ((missing_count++))
    check_secret "PROD_DB_NAME" "production" "Database" || ((missing_count++))
    check_secret "PROD_DB_USER" "production" "Database" || ((missing_count++))
    check_secret "PROD_DB_PASSWORD" "production" "Database" || ((missing_count++))
    echo
    
    # Summary
    if [[ $missing_count -eq 0 ]]; then
        print_success "🎉 All secrets are properly configured!"
        echo
        echo -e "${GREEN}Your deployment workflows should work correctly.${NC}"
        return 0
    else
        print_error "❌ Found $missing_count missing secrets"
        echo
        echo -e "${YELLOW}Setup instructions:${NC}"
        echo "1. Run: ./scripts/setup-github-deployment.sh (for most secrets)"
        echo "2. Run: ./scripts/setup-ssh-keys.sh (for SSH keys)"
        echo "3. Re-run this validation script to verify"
        return 1
    fi
}

# Function to check AWS setup
validate_aws_setup() {
    print_status "Validating AWS IAM setup..."
    
    local user_name="github-actions-deployment"
    local policy_name="GitHubActionsSecurityGroupPolicy"
    
    # Check if IAM user exists
    if aws iam get-user --user-name "$user_name" &> /dev/null; then
        print_success "✅ IAM user exists: $user_name"
        
        # Check if policy exists
        local policy_arn="arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/$policy_name"
        if aws iam get-policy --policy-arn "$policy_arn" &> /dev/null; then
            print_success "✅ IAM policy exists: $policy_name"
            
            # Check if policy is attached to user
            if aws iam list-attached-user-policies --user-name "$user_name" | grep -q "$policy_name"; then
                print_success "✅ Policy is attached to user"
                return 0
            else
                print_error "❌ Policy is not attached to user"
                echo "Run: aws iam attach-user-policy --user-name $user_name --policy-arn $policy_arn"
                return 1
            fi
        else
            print_error "❌ IAM policy missing: $policy_name"
            echo "Run: ./scripts/setup-github-deployment.sh to create it"
            return 1
        fi
    else
        print_error "❌ IAM user missing: $user_name"
        echo "Run: ./scripts/setup-github-deployment.sh to create it"
        return 1
    fi
}

# Main function
main() {
    echo -e "${BLUE}=== GitHub Deployment Configuration Validator ===${NC}"
    echo
    
    check_github_cli
    check_aws_cli
    
    local exit_code=0
    
    validate_aws_setup || exit_code=1
    echo
    validate_secrets || exit_code=1
    
    exit $exit_code
}

# Run main function
main "$@"
