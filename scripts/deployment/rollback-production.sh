#!/bin/bash

# rollback-production.sh - Rollback production deployment
# This script provides a safe way to rollback production services

set -euo pipefail

# Configuration
STACK_NAME="trends-earth-prod"
SERVICES=("manager" "worker" "beat")
LOG_FILE="/var/log/trends-earth-rollback.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOG_FILE}"
}

# Error handling
error_exit() {
    echo -e "${RED}ERROR: $1${NC}" >&2
    log "ERROR: $1"
    exit 1
}

# Warning function
warning() {
    echo -e "${YELLOW}WARNING: $1${NC}"
    log "WARNING: $1"
}

# Success function
success() {
    echo -e "${GREEN}SUCCESS: $1${NC}"
    log "SUCCESS: $1"
}

# Check if Docker Swarm is active
check_swarm() {
    if ! docker info | grep -q "Swarm: active"; then
        error_exit "Docker Swarm is not active"
    fi
}

# Check if stack exists
check_stack() {
    if ! docker stack ls | grep -q "${STACK_NAME}"; then
        error_exit "Stack '${STACK_NAME}' not found"
    fi
}

# Get service status
get_service_status() {
    local service_name="$1"
    docker service ps "${STACK_NAME}_${service_name}" --format "table {{.Name}}\t{{.CurrentState}}\t{{.DesiredState}}" 2>/dev/null || echo "Service not found"
}

# Perform rollback for a specific service
rollback_service() {
    local service_name="$1"
    local full_service_name="${STACK_NAME}_${service_name}"
    
    log "Starting rollback for service: ${service_name}"
    
    # Check if service exists
    if ! docker service ls | grep -q "${full_service_name}"; then
        warning "Service '${full_service_name}' not found, skipping"
        return 0
    fi
    
    # Show current status
    log "Current status of ${service_name}:"
    get_service_status "${service_name}"
    
    # Perform rollback
    log "Rolling back ${service_name}..."
    if docker service rollback "${full_service_name}"; then
        success "Rollback initiated for ${service_name}"
    else
        error_exit "Failed to rollback ${service_name}"
    fi
    
    # Wait for rollback to complete
    log "Waiting for ${service_name} rollback to complete..."
    local max_wait=300  # 5 minutes
    local wait_time=0
    
    while [ ${wait_time} -lt ${max_wait} ]; do
        local status=$(docker service ps "${full_service_name}" --format "{{.CurrentState}}" | head -n1)
        if [[ "${status}" == "Running"* ]]; then
            success "${service_name} rollback completed successfully"
            break
        elif [[ "${status}" == "Failed" ]]; then
            error_exit "${service_name} rollback failed"
        fi
        
        sleep 10
        wait_time=$((wait_time + 10))
        log "Waiting for ${service_name}... (${wait_time}/${max_wait}s)"
    done
    
    if [ ${wait_time} -ge ${max_wait} ]; then
        error_exit "Timeout waiting for ${service_name} rollback to complete"
    fi
}

# Health check
health_check() {
    log "Performing health check..."
    
    local max_attempts=30
    local attempt=1
    
    while [ ${attempt} -le ${max_attempts} ]; do
        if curl -f http://localhost:3001/api-health > /dev/null 2>&1; then
            success "Health check passed"
            return 0
        else
            log "Health check attempt ${attempt}/${max_attempts} failed, retrying..."
            sleep 10
            attempt=$((attempt + 1))
        fi
    done
    
    error_exit "Health check failed after ${max_attempts} attempts"
}

# Main rollback function
main() {
    log "=== Production Rollback Started ==="
    
    # Pre-flight checks
    check_swarm
    check_stack
    
    # Confirm rollback
    echo -e "${YELLOW}This will rollback all production services to their previous version.${NC}"
    echo -e "${YELLOW}Current stack status:${NC}"
    docker stack services "${STACK_NAME}"
    echo ""
    
    read -p "Are you sure you want to proceed with the rollback? (yes/no): " confirm
    if [[ "${confirm}" != "yes" ]]; then
        log "Rollback cancelled by user"
        exit 0
    fi
    
    # Perform rollback for each service
    for service in "${SERVICES[@]}"; do
        rollback_service "${service}"
        echo ""
    done
    
    # Health check
    health_check
    
    # Show final status
    log "Final service status:"
    docker stack services "${STACK_NAME}"
    
    success "Production rollback completed successfully!"
    log "=== Production Rollback Completed ==="
}

# Script help
show_help() {
    cat << EOF
Usage: $0 [options]

Production rollback script for Trends Earth API.

Options:
    -h, --help     Show this help message
    -s SERVICE     Rollback specific service only (manager|worker|beat)
    -f, --force    Skip confirmation prompt
    --dry-run      Show what would be done without executing

Examples:
    $0                    # Rollback all services with confirmation
    $0 -s manager         # Rollback only the manager service
    $0 -f                 # Rollback all services without confirmation
    $0 --dry-run          # Show rollback plan without executing

EOF
}

# Parse command line arguments
FORCE=false
DRY_RUN=false
SPECIFIC_SERVICE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -s)
            SPECIFIC_SERVICE="$2"
            shift 2
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            error_exit "Unknown option: $1"
            ;;
    esac
done

# Validate specific service
if [[ -n "${SPECIFIC_SERVICE}" ]]; then
    if [[ ! " ${SERVICES[@]} " =~ " ${SPECIFIC_SERVICE} " ]]; then
        error_exit "Invalid service: ${SPECIFIC_SERVICE}. Valid services: ${SERVICES[*]}"
    fi
    SERVICES=("${SPECIFIC_SERVICE}")
fi

# Dry run mode
if [[ "${DRY_RUN}" == "true" ]]; then
    echo "DRY RUN MODE - No changes will be made"
    echo "Would rollback the following services: ${SERVICES[*]}"
    echo "Stack: ${STACK_NAME}"
    exit 0
fi

# Force mode
if [[ "${FORCE}" == "true" ]]; then
    log "Force mode enabled - skipping confirmation"
else
    # Run main function with confirmation
    main
    exit 0
fi

# Force mode execution
log "=== Production Rollback Started (Force Mode) ==="
check_swarm
check_stack

for service in "${SERVICES[@]}"; do
    rollback_service "${service}"
done

health_check
success "Production rollback completed successfully!"
log "=== Production Rollback Completed ==="
