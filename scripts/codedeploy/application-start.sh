#!/bin/bash
# ============================================================================
# ApplicationStart Hook - Start the application
# ============================================================================
# This script starts the Docker services using docker stack deploy.
# It handles database migrations and ensures all services are running.
# ============================================================================

set -e

# Source common functions and configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

log_info "ApplicationStart hook started"

# Detect environment
ENVIRONMENT=$(detect_environment)
log_info "Detected environment: $ENVIRONMENT"

# Set variables
APP_DIR=$(get_app_directory "$ENVIRONMENT")
STACK_NAME=$(get_stack_name "$ENVIRONMENT")
COMPOSE_FILE=$(get_compose_file "$ENVIRONMENT")

cd "$APP_DIR"
log_info "Working directory: $APP_DIR"
log_info "Stack name: $STACK_NAME"
log_info "Compose file: $COMPOSE_FILE"

# ============================================================================
# Load Environment Variables
# ============================================================================

ENV_FILE=$(get_env_file "$ENVIRONMENT")
if [ -f "$ENV_FILE" ]; then
    log_info "Loading environment variables from $ENV_FILE"
    set -a
    source "$ENV_FILE"
    set +a
else
    log_error "Environment file not found: $ENV_FILE"
    exit 1
fi

# Export Docker registry for compose
export DOCKER_REGISTRY="$ECR_REGISTRY"

# ============================================================================
# Verify Compose File
# ============================================================================

if [ ! -f "$COMPOSE_FILE" ]; then
    log_error "Compose file not found: $COMPOSE_FILE"
    log_error "Available files:"
    ls -la "$APP_DIR"/*.yml 2>/dev/null || echo "No .yml files found"
    exit 1
fi

log_info "Validating compose file..."
if ! docker compose -f "$COMPOSE_FILE" config > /dev/null 2>&1; then
    log_warning "Compose file validation warning (may be expected for swarm mode)"
fi

# ============================================================================
# Check for Active Execution Services
# ============================================================================

ACTIVE_EXECUTIONS=$(docker service ls --filter "name=execution-" --format "{{.Name}}" 2>/dev/null | wc -l)
if [ "$ACTIVE_EXECUTIONS" -gt 0 ]; then
    log_warning "Found $ACTIVE_EXECUTIONS active execution services"
    log_info "Using rolling update to preserve running executions"
    docker service ls --filter "name=execution-" --format "table {{.Name}}\t{{.Replicas}}\t{{.Image}}"
fi

# ============================================================================
# Deploy Stack
# ============================================================================

log_info "Deploying stack: $STACK_NAME"

# Deploy with retry logic
MAX_ATTEMPTS=3
ATTEMPT=1

while [ $ATTEMPT -le $MAX_ATTEMPTS ]; do
    log_info "Stack deploy attempt $ATTEMPT/$MAX_ATTEMPTS..."
    
    if docker stack deploy \
        -c "$COMPOSE_FILE" \
        --with-registry-auth \
        --resolve-image always \
        "$STACK_NAME"; then
        log_success "Stack deploy command succeeded"
        break
    else
        log_warning "Stack deploy failed on attempt $ATTEMPT"
        if [ $ATTEMPT -ge $MAX_ATTEMPTS ]; then
            log_error "Stack deploy failed after $MAX_ATTEMPTS attempts"
            exit 1
        fi
        sleep 10
        ATTEMPT=$((ATTEMPT + 1))
    fi
done

# ============================================================================
# Wait for Services to Start
# ============================================================================

log_info "Waiting for services to initialize..."
sleep 15

# Monitor service status
log_info "Current service status:"
docker service ls --filter "name=${STACK_NAME}_" --format "table {{.Name}}\t{{.Replicas}}\t{{.Image}}"

# Wait for services to reach desired state
MAX_WAIT=180
WAIT_TIME=0

while [ $WAIT_TIME -lt $MAX_WAIT ]; do
    # Check if all services are running
    PENDING_SERVICES=$(docker service ls --filter "name=${STACK_NAME}_" \
        --format "{{.Replicas}}" 2>/dev/null | grep -v "1/1" | grep -v "2/2" | wc -l)
    
    # Exclude migrate service which exits after completion
    MIGRATE_STATUS=$(docker service ps "${STACK_NAME}_migrate" \
        --format "{{.CurrentState}}" 2>/dev/null | head -1 || echo "")
    
    if [ "$PENDING_SERVICES" -le 1 ]; then
        # Only migrate service might not be 1/1 (it completes and exits)
        if [ -z "$MIGRATE_STATUS" ] || echo "$MIGRATE_STATUS" | grep -q "Complete"; then
            log_success "All services are running"
            break
        fi
    fi
    
    log_info "Waiting for services... ($WAIT_TIME/$MAX_WAIT seconds)"
    docker service ls --filter "name=${STACK_NAME}_" --format "table {{.Name}}\t{{.Replicas}}"
    
    sleep 10
    WAIT_TIME=$((WAIT_TIME + 10))
done

if [ $WAIT_TIME -ge $MAX_WAIT ]; then
    log_warning "Some services may not be fully ready after $MAX_WAIT seconds"
fi

# ============================================================================
# Wait for Database Migration
# ============================================================================

log_info "Checking migration service status..."
MAX_MIGRATE_WAIT=300
MIGRATE_WAIT=0

while [ $MIGRATE_WAIT -lt $MAX_MIGRATE_WAIT ]; do
    MIGRATE_STATUS=$(docker service ps "${STACK_NAME}_migrate" \
        --format "{{.CurrentState}}" --no-trunc 2>/dev/null | head -1)
    
    if echo "$MIGRATE_STATUS" | grep -q "Complete"; then
        log_success "Database migrations completed successfully"
        break
    elif echo "$MIGRATE_STATUS" | grep -q "Failed\|Rejected"; then
        log_error "Database migration failed!"
        log_error "Status: $MIGRATE_STATUS"
        log_info "Migration service logs:"
        docker service logs --tail 50 "${STACK_NAME}_migrate" 2>/dev/null || true
        exit 1
    else
        log_info "Waiting for migrations... ($MIGRATE_WAIT/$MAX_MIGRATE_WAIT seconds)"
        log_info "Current status: $MIGRATE_STATUS"
        sleep 10
        MIGRATE_WAIT=$((MIGRATE_WAIT + 10))
    fi
done

if [ $MIGRATE_WAIT -ge $MAX_MIGRATE_WAIT ]; then
    log_warning "Migration did not complete within timeout, checking status..."
    docker service logs --tail 20 "${STACK_NAME}_migrate" 2>/dev/null || true
fi

# ============================================================================
# Final Service Status
# ============================================================================

log_info "Final service status:"
docker service ls --filter "name=${STACK_NAME}_" --format "table {{.Name}}\t{{.Replicas}}\t{{.Image}}"

log_success "ApplicationStart hook completed"
exit 0
