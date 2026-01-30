#!/bin/bash
# ============================================================================
# AfterInstall Hook - Pull Docker images and prepare application
# ============================================================================
# This script is executed after files are copied to the instance.
# It pulls the pre-built Docker images from ECR.
# ============================================================================

set -e

# Source common functions and configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

log_info "AfterInstall hook started"

# ============================================================================
# Check if this node is the swarm leader
# Only the leader needs to pull images; other nodes can skip
# ============================================================================
if ! is_swarm_leader; then
    log_info "This node is not the Swarm leader - skipping image pull"
    log_success "AfterInstall hook completed (non-leader node)"
    exit 0
fi

log_info "This node is the Swarm leader - pulling images"

# Detect environment
ENVIRONMENT=$(detect_environment)
log_info "Detected environment: $ENVIRONMENT"

# Set application directory
APP_DIR=$(get_app_directory "$ENVIRONMENT")
log_info "Application directory: $APP_DIR"

cd "$APP_DIR"

# ============================================================================
# Load Environment Variables
# ============================================================================

ENV_FILE=$(get_env_file "$ENVIRONMENT")
log_info "Loading environment variables from $ENV_FILE"
# Use safe_source_env to handle special characters (# & etc) in values
# This reads line-by-line instead of bash 'source' which interprets special chars
if ! safe_source_env "$ENV_FILE"; then
    log_error "Failed to load environment file: $ENV_FILE"
    exit 1
fi

# ============================================================================
# Verify ECR Configuration
# ============================================================================

if [ -z "$ECR_REGISTRY" ] || [ -z "$API_IMAGE" ]; then
    log_error "ECR images not configured!"
    log_error "Required environment variables: ECR_REGISTRY, API_IMAGE"
    log_error "ECR_REGISTRY=$ECR_REGISTRY"
    log_error "API_IMAGE=$API_IMAGE"
    exit 1
fi

log_info "ECR Registry: $ECR_REGISTRY"
log_info "API Image: $API_IMAGE"

# ============================================================================
# Login to ECR and Pull Images
# ============================================================================

log_info "Logging in to Amazon ECR..."
ecr_login "$ECR_REGISTRY" || {
    log_error "Failed to log in to ECR"
    exit 1
}

log_info "Pulling pre-built API image from ECR..."
log_info "Image: $API_IMAGE"

if ! docker pull "$API_IMAGE"; then
    log_error "Failed to pull API image: $API_IMAGE"
    exit 1
fi

log_success "Successfully pulled API image"

# Tag the image for use by docker-compose
# The compose files reference ${DOCKER_REGISTRY}/trendsearth-api:latest
COMPOSE_TAG="${ECR_REGISTRY}/trendsearth-api:${ENVIRONMENT}-latest"
log_info "Tagging image as: $COMPOSE_TAG"
docker tag "$API_IMAGE" "$COMPOSE_TAG"

# Also tag as the specific version expected by compose
docker tag "$API_IMAGE" "${ECR_REGISTRY}/trendsearth-api:latest"

log_info "Image tags created:"
docker images --filter "reference=*/trendsearth-api" --format "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}"

# ============================================================================
# Set Permissions
# ============================================================================

log_info "Setting file permissions..."
chown -R ubuntu:ubuntu "$APP_DIR"
chmod +x "$APP_DIR/scripts/"*.sh 2>/dev/null || true
chmod +x "$APP_DIR/scripts/codedeploy/"*.sh 2>/dev/null || true
chmod +x "$APP_DIR/entrypoint.sh" 2>/dev/null || true

# ============================================================================
# Save Deployment Metadata
# ============================================================================

DEPLOY_TAG=$(echo "$API_IMAGE" | sed 's/.*://')
echo "$DEPLOY_TAG" > "$APP_DIR/.deploy_tag"
log_info "Saved deployment tag: $DEPLOY_TAG"

# Save deployment timestamp
date -u +"%Y-%m-%d %H:%M:%S UTC" > "$APP_DIR/.deploy_timestamp"

log_success "AfterInstall hook completed"
exit 0
