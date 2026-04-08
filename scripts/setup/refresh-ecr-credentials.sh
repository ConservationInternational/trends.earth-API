#!/bin/bash
# ============================================================================
# Refresh ECR Credentials in Docker Swarm Raft Store
# ============================================================================
# This script refreshes ECR authentication tokens and pushes them into the
# Swarm raft store so that all nodes can pull images from ECR.
#
# WHY THIS IS NEEDED:
#   - ECR tokens expire after 12 hours
#   - Docker Swarm caches registry credentials in the raft store
#   - When a container is rescheduled (OOM kill, node failure, etc.), Swarm
#     needs valid credentials to pull the image on any node
#   - A separate cron job prunes old images every 2 hours, so cached images
#     cannot be relied upon — fresh pulls are common
#
# WHAT IT DOES:
#   1. Verifies this node is the Swarm leader (only leader can update raft)
#   2. Logs in to ECR to get a fresh token
#   3. Runs `docker service update --with-registry-auth` on all services
#      that use ECR images, which pushes the fresh token into the raft store
#
# INSTALLATION:
#   Install the systemd timer to run this every 4 hours:
#
#     sudo cp scripts/setup/ecr-refresh.service /etc/systemd/system/
#     sudo cp scripts/setup/ecr-refresh.timer /etc/systemd/system/
#     sudo systemctl daemon-reload
#     sudo systemctl enable --now ecr-refresh.timer
#
# ============================================================================

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-273676533378}"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Logging (compatible with journald)
log_info()    { echo "[INFO]  $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_success() { echo "[OK]    $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_warning() { echo "[WARN]  $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error()   { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

# ============================================================================
# Pre-flight Checks
# ============================================================================

# Docker must be running
if ! docker info >/dev/null 2>&1; then
    log_error "Docker daemon is not reachable"
    exit 1
fi

# This node must be the Swarm leader — only the leader can update the raft store
MANAGER_STATUS=$(docker node ls --format '{{.Self}} {{.ManagerStatus}}' 2>/dev/null \
    | awk '$1=="true" {print $2}')

if [ "$MANAGER_STATUS" != "Leader" ]; then
    log_info "Not the Swarm leader (status: ${MANAGER_STATUS:-unknown}) — skipping"
    exit 0
fi

log_info "This node is the Swarm leader — proceeding with ECR credential refresh"

# AWS CLI must be available
if ! command -v aws &>/dev/null; then
    log_error "AWS CLI is not installed"
    exit 1
fi

# ============================================================================
# Refresh ECR Token (local Docker login)
# ============================================================================

log_info "Logging in to ECR: ${ECR_REGISTRY}"
if ! aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "$ECR_REGISTRY"; then
    log_error "ECR login failed"
    exit 1
fi
log_success "ECR login succeeded"

# ============================================================================
# Push Credentials into Swarm Raft Store
# ============================================================================
# `docker service update --with-registry-auth` reads the local Docker config
# and pushes the credentials into the Swarm raft store for that service.
# We do a no-op update (no image change) so only the auth token is refreshed.
# ============================================================================

UPDATED=0
FAILED=0

# Find all Swarm services whose image comes from our ECR registry
ECR_SERVICES=$(docker service ls --format '{{.Name}} {{.Image}}' 2>/dev/null \
    | grep "$ECR_REGISTRY" \
    | awk '{print $1}' || true)

if [ -z "$ECR_SERVICES" ]; then
    log_warning "No Swarm services found using ECR registry ${ECR_REGISTRY}"
    exit 0
fi

log_info "Found ECR-backed services:"
for svc in $ECR_SERVICES; do
    echo "  - $svc"
done

for svc in $ECR_SERVICES; do
    log_info "Updating credentials for service: $svc"
    if docker service update --with-registry-auth --detach "$svc" >/dev/null 2>&1; then
        log_success "Credentials updated: $svc"
        UPDATED=$((UPDATED + 1))
    else
        log_error "Failed to update credentials: $svc"
        FAILED=$((FAILED + 1))
    fi
done

# ============================================================================
# Summary
# ============================================================================

log_info "ECR credential refresh complete: $UPDATED updated, $FAILED failed"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi

exit 0
