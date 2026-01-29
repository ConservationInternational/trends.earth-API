#!/bin/bash
# ============================================================================
# BeforeInstall Hook - Prepare environment for new deployment
# ============================================================================
# This script is executed after ApplicationStop and before files are copied.
# It prepares the environment, installs dependencies, and cleans up old files.
#
# SINGLE-INSTANCE SUPPORT:
# The GitHub workflow modifies appspec.yml to deploy directly to:
#   - /opt/trendsearth-api-staging (for staging)
#   - /opt/trendsearth-api-production (for production)
# This prevents race conditions between simultaneous deployments.
# ============================================================================

set -e

# Source common functions and configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

log_info "BeforeInstall hook started"

# Detect environment
ENVIRONMENT=$(detect_environment)
log_info "Detected environment: $ENVIRONMENT"

# Environment-specific application directory
APP_DIR=$(get_app_directory "$ENVIRONMENT")
log_info "Application directory: $APP_DIR"

# Create application directory if it doesn't exist
if [ ! -d "$APP_DIR" ]; then
    log_info "Creating application directory: $APP_DIR"
    mkdir -p "$APP_DIR"
fi

# Set ownership
chown -R ubuntu:ubuntu "$APP_DIR"

# ============================================================================
# Verify Docker Installation
# ============================================================================

log_info "Checking Docker installation..."
if ! command -v docker &> /dev/null; then
    log_error "Docker is not installed. Please install Docker first."
    exit 1
fi

if ! systemctl is-active --quiet docker; then
    log_info "Starting Docker service..."
    systemctl start docker
fi

# Ensure Docker Swarm is initialized
if ! docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null | grep -q "active"; then
    log_info "Initializing Docker Swarm..."
    docker swarm init --advertise-addr $(hostname -I | awk '{print $1}') || {
        log_warning "Swarm init failed, may already be part of a swarm"
    }
fi

# Ensure docker-compose is available
log_info "Checking Docker Compose installation..."
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    log_error "Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# ============================================================================
# Docker Cleanup Strategy
# ============================================================================
# Since all images are stored on ECR, we can safely clean up local Docker
# resources. Images are always pulled fresh from ECR during deployment.
#
# Cleanup levels:
#   1. Always: Remove stopped containers, dangling images, unused networks
#   2. Always: Remove images older than 7 days (they're on ECR anyway)
#   3. If disk > 70%: Aggressive cleanup - remove all unused images
#   4. If disk > 85%: Emergency cleanup - remove everything including cache
# ============================================================================

log_info "Checking Docker disk usage before cleanup..."
DISK_USAGE=$(df /var/lib/docker 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
log_info "Current Docker disk usage: ${DISK_USAGE:-unknown}%"

# Show Docker disk usage breakdown
log_info "Docker disk usage breakdown:"
docker system df 2>/dev/null || true

# Level 1: Basic cleanup (always run)
log_info "Removing stopped containers..."
docker container prune -f 2>/dev/null || true

log_info "Removing dangling images..."
docker image prune -f 2>/dev/null || true

log_info "Removing unused networks..."
docker network prune -f 2>/dev/null || true

# Level 2: Remove old images (always run - images are on ECR)
log_info "Removing Docker images older than 7 days..."
docker image prune -a --filter "until=168h" -f 2>/dev/null || true

# Level 3: Aggressive cleanup if disk usage > 70%
if [ -n "$DISK_USAGE" ] && [ "$DISK_USAGE" -gt 70 ]; then
    log_warning "Disk usage above 70%, performing aggressive cleanup..."
    docker image prune -a -f 2>/dev/null || true
fi

# Level 4: Emergency cleanup if disk usage > 85%
if [ -n "$DISK_USAGE" ] && [ "$DISK_USAGE" -gt 85 ]; then
    log_warning "Disk usage above 85%, performing emergency cleanup..."
    docker system prune -a -f --volumes 2>/dev/null || true
fi

# Show disk usage after cleanup
log_info "Docker disk usage after cleanup:"
docker system df 2>/dev/null || true

# ============================================================================
# Verify AWS CLI and ECR Access
# ============================================================================

log_info "Verifying AWS CLI installation..."
if ! command -v aws &> /dev/null; then
    log_error "AWS CLI is not installed. Please install AWS CLI first."
    exit 1
fi

# Verify EC2 instance role has access to ECR
log_info "Testing ECR access via instance role..."
REGION=$(get_aws_region)
if ! aws ecr get-login-password --region "$REGION" > /dev/null 2>&1; then
    log_error "Cannot authenticate to ECR. Ensure EC2 instance has proper IAM role."
    exit 1
fi
log_success "ECR access verified"

log_success "BeforeInstall hook completed"
exit 0
