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

# Safely source an environment file without bash interpretation of special chars
# This reads line-by-line and exports variables, handling # and & in values correctly
# Docker env_file format: VAR=value (no quotes needed)
# This function is necessary because 'source file.env' interprets special chars
safe_source_env() {
    local file="$1"
    
    if [ ! -f "$file" ]; then
        log_error "Environment file not found: $file"
        return 1
    fi
    
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip empty lines and comments
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        
        # Skip lines without =
        [[ "$line" != *"="* ]] && continue
        
        # Extract key and value (split on first = only)
        local key="${line%%=*}"
        local value="${line#*=}"
        
        # Skip if key is empty
        [[ -z "$key" ]] && continue
        
        # Strip surrounding quotes if present (for backwards compatibility)
        # This handles both 'value' and "value" formats
        if [[ "$value" =~ ^\'(.*)\'$ ]]; then
            value="${BASH_REMATCH[1]}"
        elif [[ "$value" =~ ^\"(.*)\"$ ]]; then
            value="${BASH_REMATCH[1]}"
        fi
        
        # Export the variable
        export "$key=$value"
    done < "$file"
    
    return 0
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

# Check if this node is the active Swarm leader
# Returns 0 if this node is the leader, 1 otherwise
# When deploying to multiple nodes in a Swarm, only the leader should
# execute stack deploy commands - Swarm handles distribution to other nodes
is_swarm_leader() {
    # Check if Docker is available
    if ! docker info >/dev/null 2>&1; then
        log_warning "Docker daemon unreachable"
        return 1
    fi
    
    # Get this node's manager status
    local manager_status
    manager_status=$(docker node ls --format '{{.Self}} {{.ManagerStatus}}' 2>/dev/null | awk '$1=="true" {print $2}')
    
    if [ -z "$manager_status" ]; then
        log_warning "This node is not part of the swarm or manager status unknown"
        return 1
    fi
    
    if [ "$manager_status" != "Leader" ]; then
        log_info "Node is a swarm manager but not the leader (status: $manager_status)"
        return 1
    fi
    
    log_success "Node is the active swarm leader"
    return 0
}

# Check if we should skip deployment on this node (non-leader)
# Exits with 0 if not the leader (skip), continues if leader
check_swarm_leader_or_skip() {
    log_info "Checking swarm manager status..."
    if ! is_swarm_leader; then
        log_info "Skipping deployment on this node (not the swarm leader)"
        exit 0
    fi
}

# Check if stack networks exist and are healthy
# Returns 0 if healthy, 1 if needs recovery
check_stack_networks() {
    local stack_name="$1"
    
    # Expected networks for our stack
    local backend_network="${stack_name}_backend"
    local execution_network="${stack_name}_execution"
    
    # Check if networks exist
    if ! docker network inspect "$backend_network" >/dev/null 2>&1; then
        log_warning "Network $backend_network does not exist"
        return 1
    fi
    
    if ! docker network inspect "$execution_network" >/dev/null 2>&1; then
        log_warning "Network $execution_network does not exist"
        return 1
    fi
    
    log_success "Stack networks are healthy"
    return 0
}

# Recover a stack that's in a bad state by removing and redeploying
# This fixes issues where networks exist but tasks are stuck in "New" state
recover_stack() {
    local stack_name="$1"
    local compose_file="$2"
    
    log_warning "Attempting stack recovery for $stack_name..."
    
    # Check if stack exists
    if docker stack ls --format "{{.Name}}" 2>/dev/null | grep -q "^${stack_name}$"; then
        log_info "Removing existing stack to recover from bad state..."
        docker stack rm "$stack_name" 2>/dev/null || true
        
        # Wait for stack resources to be fully removed
        log_info "Waiting for stack resources to be cleaned up..."
        local wait_count=0
        local max_wait=60
        while [ $wait_count -lt $max_wait ]; do
            # Check if any stack resources still exist
            local remaining=$(docker service ls --filter "name=${stack_name}_" --format "{{.Name}}" 2>/dev/null | wc -l)
            if [ "$remaining" -eq 0 ]; then
                # Also wait for networks to be removed
                if ! docker network inspect "${stack_name}_backend" >/dev/null 2>&1 && \
                   ! docker network inspect "${stack_name}_execution" >/dev/null 2>&1; then
                    log_success "Stack resources cleaned up"
                    break
                fi
            fi
            sleep 2
            wait_count=$((wait_count + 2))
        done
        
        if [ $wait_count -ge $max_wait ]; then
            log_warning "Timeout waiting for cleanup, proceeding anyway..."
        fi
        
        # Extra wait for Docker to fully release resources
        sleep 5
    fi
    
    return 0
}

# Check for stuck services (tasks in "New" or "Pending" state for too long)
check_for_stuck_services() {
    local stack_name="$1"
    
    # Check for tasks stuck in "New" state with no node assigned
    local stuck_tasks=$(docker service ls --filter "name=${stack_name}_" --format "{{.Name}} {{.Replicas}}" 2>/dev/null | \
        grep -E "0/[0-9]+" | head -5 || echo "")
    
    if [ -n "$stuck_tasks" ]; then
        log_warning "Found services with 0 running replicas:"
        echo "$stuck_tasks"
        
        # Check if these are actually stuck (no node assignment)
        for service_info in $stuck_tasks; do
            local service_name=$(echo "$service_info" | awk '{print $1}')
            local task_state=$(docker service ps "$service_name" --format "{{.CurrentState}} {{.Node}}" 2>/dev/null | head -1)
            if echo "$task_state" | grep -qi "new\|pending" && echo "$task_state" | grep -qE "^[A-Za-z]+ *$"; then
                log_warning "Service $service_name has tasks stuck without node assignment"
                return 1
            fi
        done
    fi
    
    return 0
}

# Export the functions for use in other scripts
export -f log_info log_success log_warning log_error
export -f detect_environment get_app_directory get_compose_file get_env_file
export -f get_stack_name get_api_port wait_for_service get_aws_region ecr_login
export -f is_swarm_leader check_swarm_leader_or_skip safe_source_env
export -f check_stack_networks recover_stack check_for_stuck_services
