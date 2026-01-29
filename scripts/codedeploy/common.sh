#!/bin/bash
# ============================================================================
# Common Functions for CodeDeploy Scripts
# ============================================================================
# This script contains shared functions and configuration used by all
# CodeDeploy lifecycle hook scripts.
#
# SINGLE-INSTANCE SUPPORT:
# Both staging and production can run on the same EC2 instance:
#   - Production: /opt/trendsearth-api-production (port 3001)
#   - Staging:    /opt/trendsearth-api-staging    (port 3002)
# ============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# Detect environment from CodeDeploy deployment group or hostname
detect_environment() {
    # First check CodeDeploy environment variable
    if [ -n "$DEPLOYMENT_GROUP_NAME" ]; then
        if echo "$DEPLOYMENT_GROUP_NAME" | grep -qi "staging"; then
            echo "staging"
            return
        elif echo "$DEPLOYMENT_GROUP_NAME" | grep -qi "production\|prod"; then
            echo "production"
            return
        fi
    fi
    
    # Check hostname
    HOSTNAME=$(hostname)
    if echo "$HOSTNAME" | grep -qi "staging"; then
        echo "staging"
        return
    elif echo "$HOSTNAME" | grep -qi "production\|prod"; then
        echo "production"
        return
    fi
    
    # Check for environment file markers
    if [ -f "/opt/trendsearth-api-staging/staging.env" ]; then
        echo "staging"
        return
    elif [ -f "/opt/trendsearth-api-production/prod.env" ]; then
        echo "production"
        return
    fi
    
    # Default to staging for safety
    log_warning "Could not determine environment, defaulting to staging"
    echo "staging"
}

# Get application directory based on environment
# Each environment has its own directory to support single-instance deployments
get_app_directory() {
    local environment="$1"
    echo "/opt/trendsearth-api-${environment}"
}

# Get docker-compose file based on environment
get_compose_file() {
    local environment="$1"
    if [ "$environment" = "staging" ]; then
        echo "docker-compose.staging.yml"
    else
        echo "docker-compose.prod.yml"
    fi
}

# Get docker stack/project name based on environment
get_stack_name() {
    local environment="$1"
    echo "trends-earth-${environment}"
}

# Get API port based on environment
get_api_port() {
    local environment="$1"
    if [ "$environment" = "production" ]; then
        echo "3001"
    else
        echo "3002"
    fi
}

# Get environment file based on environment
# Uses the same naming convention as docker-compose files: staging.env, prod.env
get_env_file() {
    local environment="$1"
    local app_dir=$(get_app_directory "$environment")
    if [ "$environment" = "staging" ]; then
        echo "$app_dir/staging.env"
    else
        echo "$app_dir/prod.env"
    fi
}

# Wait for a service to be healthy
wait_for_service() {
    local service_url="$1"
    local max_attempts="${2:-30}"
    local wait_time="${3:-10}"
    
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "$service_url" > /dev/null 2>&1; then
            return 0
        fi
        log_info "Waiting for service at $service_url (attempt $attempt/$max_attempts)..."
        sleep $wait_time
        attempt=$((attempt + 1))
    done
    
    return 1
}

# Get AWS region from instance metadata or default
get_aws_region() {
    # Try IMDSv2 first
    local TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || echo "")
    
    if [ -n "$TOKEN" ]; then
        REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
            http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || echo "")
    else
        # Fallback to IMDSv1
        REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || echo "")
    fi
    
    # Default to us-east-1 if detection fails
    if [ -z "$REGION" ]; then
        REGION="us-east-1"
    fi
    
    echo "$REGION"
}

# Login to ECR
ecr_login() {
    local region=$(get_aws_region)
    local ecr_registry="$1"
    
    log_info "Logging in to Amazon ECR in region $region..."
    aws ecr get-login-password --region "$region" | \
        docker login --username AWS --password-stdin "$ecr_registry" || {
        log_error "Failed to log in to ECR"
        return 1
    }
    
    log_success "Successfully logged in to ECR"
    return 0
}

# Export the functions for use in other scripts
export -f log_info log_success log_warning log_error
export -f detect_environment get_app_directory get_compose_file get_env_file
export -f get_stack_name get_api_port wait_for_service get_aws_region ecr_login
