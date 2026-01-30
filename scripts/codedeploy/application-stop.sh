#!/bin/bash
# ============================================================================
# ApplicationStop Hook - Gracefully stop the application
# ============================================================================
# This script is executed before the deployment begins to gracefully stop
# running services.
#
# SINGLE-INSTANCE SUPPORT:
# Only stops services for the specific environment being deployed.
# Production and staging run independently on different ports.
# ============================================================================

set -e

# Source common functions and configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

log_info "ApplicationStop hook started"

# Check if this node is the swarm leader - only leader should manage stacks
check_swarm_leader_or_skip

# Detect environment
ENVIRONMENT=$(detect_environment)
log_info "Detected environment: $ENVIRONMENT"

# Get stack name for this environment
STACK_NAME=$(get_stack_name "$ENVIRONMENT")
log_info "Stack name: $STACK_NAME"

# Check if Docker Swarm is active
if ! docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null | grep -q "active"; then
    log_info "Docker Swarm is not active, skipping service stop"
    exit 0
fi

# Check if the stack exists
if ! docker stack ls --format "{{.Name}}" 2>/dev/null | grep -q "^${STACK_NAME}$"; then
    log_info "Stack $STACK_NAME does not exist, nothing to stop"
    exit 0
fi

# ============================================================================
# Graceful Service Shutdown
# ============================================================================
# We don't remove the stack here - just scale down services for a graceful
# transition. The stack will be updated by ApplicationStart.
# ============================================================================

log_info "Preparing services for deployment..."

# Get list of services in the stack
SERVICES=$(docker service ls --filter "name=${STACK_NAME}_" --format "{{.Name}}" 2>/dev/null || echo "")

if [ -n "$SERVICES" ]; then
    log_info "Current services in stack:"
    docker service ls --filter "name=${STACK_NAME}_" --format "table {{.Name}}\t{{.Replicas}}\t{{.Image}}"
    
    # Check for active execution services that should be preserved
    # These are dynamically created script execution services
    EXECUTION_SERVICES=$(docker service ls --filter "name=execution-" --format "{{.Name}}" 2>/dev/null || echo "")
    
    if [ -n "$EXECUTION_SERVICES" ]; then
        log_warning "Active execution services detected - these will be preserved:"
        echo "$EXECUTION_SERVICES"
        log_info "Rolling update will be used to prevent disruption"
    fi
    
    log_success "Services prepared for deployment"
else
    log_info "No services found in stack $STACK_NAME"
fi

log_success "ApplicationStop hook completed"
exit 0
