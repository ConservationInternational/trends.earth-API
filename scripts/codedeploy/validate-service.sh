#!/bin/bash
# ============================================================================
# ValidateService Hook - Verify deployment success
# ============================================================================
# This script performs health checks to verify the deployment was successful.
# If validation fails, CodeDeploy will mark the deployment as failed and
# can automatically rollback.
# ============================================================================

set -e

# Source common functions and configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

log_info "ValidateService hook started"

# Detect environment
ENVIRONMENT=$(detect_environment)
log_info "Detected environment: $ENVIRONMENT"

# Set variables
APP_DIR=$(get_app_directory "$ENVIRONMENT")
STACK_NAME=$(get_stack_name "$ENVIRONMENT")
API_PORT=$(get_api_port "$ENVIRONMENT")

cd "$APP_DIR"
log_info "Application directory: $APP_DIR"
log_info "Stack name: $STACK_NAME"
log_info "API port: $API_PORT"

# ============================================================================
# Check Service Status
# ============================================================================

log_info "Checking service status..."
docker service ls --filter "name=${STACK_NAME}_" --format "table {{.Name}}\t{{.Replicas}}\t{{.Image}}"

# Verify API service is running
API_SERVICE="${STACK_NAME}_api"
API_REPLICAS=$(docker service ls --filter "name=${API_SERVICE}" --format "{{.Replicas}}" 2>/dev/null || echo "0/0")

if [ "$API_REPLICAS" = "0/0" ] || [ -z "$API_REPLICAS" ]; then
    log_error "API service is not running!"
    log_info "Service details:"
    docker service ps "$API_SERVICE" --format "table {{.Name}}\t{{.CurrentState}}\t{{.Error}}" 2>/dev/null || true
    exit 1
fi

log_success "API service status: $API_REPLICAS"

# Verify worker service is running
WORKER_SERVICE="${STACK_NAME}_worker"
WORKER_REPLICAS=$(docker service ls --filter "name=${WORKER_SERVICE}" --format "{{.Replicas}}" 2>/dev/null || echo "0/0")

if [ "$WORKER_REPLICAS" = "0/0" ] || [ -z "$WORKER_REPLICAS" ]; then
    log_warning "Worker service may not be running: $WORKER_REPLICAS"
else
    log_success "Worker service status: $WORKER_REPLICAS"
fi

# ============================================================================
# Health Check - API Endpoint
# ============================================================================

log_info "Performing API health check on port $API_PORT..."

HEALTH_URL="http://localhost:${API_PORT}/api-health"
MAX_ATTEMPTS=30
ATTEMPT=1

while [ $ATTEMPT -le $MAX_ATTEMPTS ]; do
    log_info "Health check attempt $ATTEMPT/$MAX_ATTEMPTS..."
    
    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    
    if [ "$HTTP_CODE" = "200" ]; then
        log_success "API health check passed (HTTP $HTTP_CODE)"
        break
    else
        log_warning "Health check returned HTTP $HTTP_CODE"
        
        if [ $ATTEMPT -ge $MAX_ATTEMPTS ]; then
            log_error "API health check failed after $MAX_ATTEMPTS attempts"
            
            # Debug information
            log_info "Debugging information:"
            echo "  - Service status:"
            docker service ps "$API_SERVICE" --format "table {{.Name}}\t{{.CurrentState}}\t{{.Error}}" 2>/dev/null || true
            
            echo "  - Recent API logs:"
            docker service logs --tail 30 "$API_SERVICE" 2>/dev/null || true
            
            echo "  - Container processes:"
            docker ps --filter "name=${STACK_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true
            
            exit 1
        fi
        
        sleep 10
        ATTEMPT=$((ATTEMPT + 1))
    fi
done

# ============================================================================
# Health Check - API Documentation
# ============================================================================

log_info "Checking API documentation endpoint..."
DOCS_URL="http://localhost:${API_PORT}/api/docs/"

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" "$DOCS_URL" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    log_success "API documentation endpoint is accessible"
else
    log_warning "API documentation endpoint returned HTTP $HTTP_CODE (non-critical)"
fi

# ============================================================================
# Health Check - Database Connectivity
# ============================================================================

log_info "Checking database connectivity via API..."
USER_ENDPOINT="http://localhost:${API_PORT}/api/v1/user/me"

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" "$USER_ENDPOINT" 2>/dev/null || echo "000")

# We expect 401 (unauthorized) or 403 (forbidden) which means the API is working
# and can reach the database
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
    log_success "API is responding correctly (HTTP $HTTP_CODE)"
else
    log_warning "User endpoint returned HTTP $HTTP_CODE (may be expected)"
fi

# ============================================================================
# Deployment Summary
# ============================================================================

log_info "Deployment validation summary:"
echo "  ✅ Stack: $STACK_NAME"
echo "  ✅ API Service: $API_REPLICAS"
echo "  ✅ Worker Service: $WORKER_REPLICAS"
echo "  ✅ API Port: $API_PORT"
echo "  ✅ Health Check: Passed"

if [ "$ENVIRONMENT" = "staging" ]; then
    log_success "Application available at: https://api-staging.trends.earth"
else
    log_success "Application available at: https://api.trends.earth"
fi

log_success "ValidateService hook completed - Deployment successful!"
exit 0
