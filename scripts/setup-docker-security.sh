#!/bin/bash

# Script to set up Docker security for non-root container operation
# This script helps configure the correct Docker group ID for container access

echo "=== Trends.Earth API Docker Security Setup ==="
echo

# Check if running on Linux/WSL
if [[ "$OSTYPE" == "linux-gnu"* ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "$WSL_DISTRO_NAME" ]]; then
    echo "✅ Detected Linux/WSL environment"
    
    # Get Docker group ID
    if command -v docker &> /dev/null; then
        if getent group docker &> /dev/null; then
            DOCKER_GID=$(getent group docker | cut -d: -f3)
            echo "✅ Docker group found with GID: $DOCKER_GID"
        else
            echo "⚠️  Docker group not found. Creating docker group..."
            sudo groupadd docker
            DOCKER_GID=$(getent group docker | cut -d: -f3)
            echo "✅ Docker group created with GID: $DOCKER_GID"
        fi
        
        # Check if current user is in docker group
        if groups $USER | grep -q docker; then
            echo "✅ User $USER is already in docker group"
        else
            echo "⚠️  Adding user $USER to docker group..."
            sudo usermod -aG docker $USER
            echo "✅ User added to docker group. Please log out and back in for changes to take effect."
        fi
    else
        echo "❌ Docker not found. Please install Docker first."
        exit 1
    fi
    
elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "✅ Detected macOS environment"
    # On macOS with Docker Desktop, the docker group is typically 'staff' or the socket has more open permissions
    DOCKER_GID=$(stat -f %g /var/run/docker.sock 2>/dev/null || echo "20")
    echo "✅ Docker socket group ID: $DOCKER_GID"
    
else
    echo "⚠️  Unsupported OS. Manual configuration may be required."
    DOCKER_GID="999"
fi

# Update or create .env file with Docker group ID
ENV_FILE="develop.env"
if [ -f "$ENV_FILE" ]; then
    if grep -q "DOCKER_GROUP_ID" "$ENV_FILE"; then
        sed -i.bak "s/DOCKER_GROUP_ID=.*/DOCKER_GROUP_ID=$DOCKER_GID/" "$ENV_FILE"
        echo "✅ Updated DOCKER_GROUP_ID in $ENV_FILE"
    else
        echo "DOCKER_GROUP_ID=$DOCKER_GID" >> "$ENV_FILE"
        echo "✅ Added DOCKER_GROUP_ID to $ENV_FILE"
    fi
else
    echo "DOCKER_GROUP_ID=$DOCKER_GID" > "$ENV_FILE"
    echo "✅ Created $ENV_FILE with DOCKER_GROUP_ID"
fi

echo
echo "=== Security Configuration Complete ==="
echo "✅ Container will now run as non-root user 'gef-api'"
echo "✅ Docker socket access configured with group ID: $DOCKER_GID"
echo "✅ Security risk significantly reduced"
echo
echo "Next steps:"
echo "1. Rebuild your containers: docker compose build"
echo "2. Start your services: docker compose -f docker-compose.develop.yml up"
echo
echo "If you encounter Docker permission issues:"
echo "1. Ensure your user is in the docker group: groups \$USER"
echo "2. Log out and back in if you were just added to the docker group"
echo "3. Restart the Docker service if needed"
