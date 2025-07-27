#!/bin/bash
# =============================================================================
# Docker Permissions Setup Script for Docker Swarm
# =============================================================================
# This script helps configure Docker permissions for the Trends.Earth API
# containers to access the Docker socket for script execution in Swarm mode.
#
# Usage: ./scripts/setup-docker-permissions.sh [environment]
#   environment: dev, staging, prod (default: staging)
#
# Note: In Swarm mode, containers can run on any node, so this script helps
# ensure consistent Docker group configuration across all nodes.
# =============================================================================

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Get environment parameter
ENVIRONMENT=${1:-staging}
ENV_FILE="${ENVIRONMENT}.env"

print_status "Setting up Docker permissions for environment: $ENVIRONMENT"
print_status "Note: For Swarm deployments, ensure all nodes have consistent docker group GIDs"

# Special handling for production
if [ "$ENVIRONMENT" = "prod" ]; then
    print_warning "Production environment detected!"
    print_warning "Ensure you have proper security measures in place for Docker socket access"
fi

echo

# Check if Docker is installed and running
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed. Please install Docker first."
    exit 1
fi

if ! docker info &> /dev/null; then
    print_error "Docker is not running or current user doesn't have access."
    print_error "Try: sudo systemctl start docker"
    print_error "Or add your user to docker group: sudo usermod -aG docker \$USER"
    exit 1
fi

print_success "Docker is installed and accessible"

# Get Docker group ID
if command -v getent &> /dev/null; then
    DOCKER_GID=$(getent group docker | cut -d: -f3 2>/dev/null)
else
    # Fallback method for systems without getent
    DOCKER_GID=$(grep '^docker:' /etc/group | cut -d: -f3 2>/dev/null)
fi

if [ -z "$DOCKER_GID" ]; then
    print_warning "Could not determine Docker group ID. Using default value 999."
    DOCKER_GID=999
else
    print_success "Found Docker group ID: $DOCKER_GID"
fi

# Check Docker socket permissions
DOCKER_SOCKET="/var/run/docker.sock"
if [ -e "$DOCKER_SOCKET" ]; then
    SOCKET_GID=$(stat -c %g "$DOCKER_SOCKET" 2>/dev/null)
    SOCKET_PERMS=$(ls -la "$DOCKER_SOCKET")
    print_status "Docker socket permissions: $SOCKET_PERMS"
    print_status "Docker socket GID: $SOCKET_GID"
    
    if [ "$DOCKER_GID" != "$SOCKET_GID" ]; then
        print_warning "Docker group GID ($DOCKER_GID) doesn't match socket GID ($SOCKET_GID)"
        print_warning "This might cause permission issues in containers"
    else
        print_success "Docker group GID matches socket GID"
    fi
else
    print_error "Docker socket not found at $DOCKER_SOCKET"
    exit 1
fi

# Create or update environment file
if [ -f "$ENV_FILE" ]; then
    print_status "Updating existing environment file: $ENV_FILE"
    
    # Update DOCKER_GROUP_ID if it exists, otherwise add it
    if grep -q "^DOCKER_GROUP_ID=" "$ENV_FILE"; then
        sed -i "s/^DOCKER_GROUP_ID=.*/DOCKER_GROUP_ID=$DOCKER_GID/" "$ENV_FILE"
        print_success "Updated DOCKER_GROUP_ID in $ENV_FILE"
    else
        echo "" >> "$ENV_FILE"
        echo "# Docker Configuration" >> "$ENV_FILE"
        echo "DOCKER_GROUP_ID=$DOCKER_GID" >> "$ENV_FILE"
        print_success "Added DOCKER_GROUP_ID to $ENV_FILE"
    fi
    
    # Update DOCKER_HOST if needed
    if ! grep -q "^DOCKER_HOST=" "$ENV_FILE"; then
        echo "DOCKER_HOST=unix:///var/run/docker.sock" >> "$ENV_FILE"
        print_success "Added DOCKER_HOST to $ENV_FILE"
    fi
else
    print_status "Creating new environment file: $ENV_FILE"
    if [ -f ".env.example" ]; then
        cp ".env.example" "$ENV_FILE"
        sed -i "s/^DOCKER_GROUP_ID=.*/DOCKER_GROUP_ID=$DOCKER_GID/" "$ENV_FILE"
        print_success "Created $ENV_FILE from .env.example and updated DOCKER_GROUP_ID"
    else
        cat > "$ENV_FILE" << EOF
# Docker Configuration
DOCKER_GROUP_ID=$DOCKER_GID
DOCKER_HOST=unix:///var/run/docker.sock

# Add other required environment variables here
EOF
        print_success "Created basic $ENV_FILE with Docker configuration"
        print_warning "You may need to add other required environment variables"
    fi
fi

echo
print_status "Verification:"
echo "  - Docker Group ID: $DOCKER_GID"
echo "  - Environment File: $ENV_FILE"
echo "  - Docker Socket: $DOCKER_SOCKET (GID: $SOCKET_GID)"

echo
print_status "Next steps for Swarm deployment:"
echo "  1. Review and complete your $ENV_FILE configuration"
echo "  2. Ensure all Swarm nodes have docker group with GID $DOCKER_GID:"
echo "     sudo groupmod -g $DOCKER_GID docker  # (run on each node)"
echo "  3. Deploy services: docker stack deploy -c docker-compose.$ENVIRONMENT.yml trendsearth"
echo "  4. Check container logs for Docker permission messages:"
echo "     docker service logs trendsearth_worker"

if [ "$DOCKER_GID" != "$SOCKET_GID" ]; then
    echo
    print_warning "Important for Swarm:"
    echo "  - All Swarm nodes must have consistent docker group GIDs"
    echo "  - Run on each node: sudo groupmod -g $DOCKER_GID docker"
    echo "  - Or adjust DOCKER_GROUP_ID in $ENV_FILE to match all nodes"
    echo "  - Restart Docker daemon after group changes: sudo systemctl restart docker"
fi

echo
print_success "Docker permissions setup completed for $ENVIRONMENT environment (Swarm ready)"
