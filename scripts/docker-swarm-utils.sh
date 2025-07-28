#!/bin/bash
# Docker Swarm Utilities for Deployment Scripts
# Shared functions for handling Docker Swarm services across nodes

# Function to check if a Docker Swarm service is running locally on this node
is_service_local() {
    local service_name="$1"
    
    # Check if container for this service is running locally
    local container_id=$(docker ps --filter "label=com.docker.swarm.service.name=${service_name}" --format "{{.ID}}" 2>/dev/null | head -1)
    if [ -n "$container_id" ]; then
        echo "$container_id"
        return 0
    fi
    
    # Check by pattern match for Docker Swarm task names (e.g., service_name.1.abc123)
    container_id=$(docker ps --format "{{.ID}} {{.Names}}" 2>/dev/null | grep "${service_name}\." | head -1 | cut -d' ' -f1)
    if [ -n "$container_id" ]; then
        echo "$container_id"
        return 0
    fi
    
    return 1
}

# Function to get the node where a Docker Swarm service is running
get_service_node() {
    local service_name="$1"
    docker service ps "$service_name" --format "{{.Node}}" --filter "desired-state=running" 2>/dev/null | head -1
}

# Function to execute a command on a Docker Swarm service, handling multi-node scenarios
exec_on_service() {
    local service_name="$1"
    local command="$2"
    local current_node=$(hostname)
    
    # First try to find container locally
    local container_id=$(is_service_local "$service_name")
    if [ $? -eq 0 ] && [ -n "$container_id" ]; then
        echo "[INFO] Executing on local container: $container_id" >&2
        docker exec "$container_id" $command
        return $?
    fi
    
    # Service is on a different node - get the node name
    local service_node=$(get_service_node "$service_name")
    if [ -z "$service_node" ]; then
        echo "[ERROR] Service $service_name not found or not running" >&2
        return 1
    fi
    
    if [ "$service_node" != "$current_node" ]; then
        echo "[ERROR] Service $service_name is running on node '$service_node', but this script is running on '$current_node'" >&2
        echo "[ERROR] In Docker Swarm multi-node setup, please run this script on the correct node:" >&2
        echo "[ERROR]   ssh $service_node" >&2
        echo "[ERROR]   cd $(pwd)" >&2
        echo "[ERROR]   $0" >&2
        return 1
    fi
    
    # This shouldn't happen, but handle it gracefully
    echo "[ERROR] Service $service_name should be local but container not found" >&2
    return 1
}

# Function to copy file to a Docker Swarm service, handling multi-node scenarios  
copy_to_service() {
    local service_name="$1"
    local src_file="$2"
    local dst_path="$3"
    local current_node=$(hostname)
    
    # First try to find container locally
    local container_id=$(is_service_local "$service_name")
    if [ $? -eq 0 ] && [ -n "$container_id" ]; then
        echo "[INFO] Copying to local container: $container_id" >&2
        docker cp "$src_file" "$container_id:$dst_path"
        return $?
    fi
    
    # Service is on a different node
    local service_node=$(get_service_node "$service_name")
    if [ -z "$service_node" ]; then
        echo "[ERROR] Service $service_name not found or not running" >&2
        return 1
    fi
    
    if [ "$service_node" != "$current_node" ]; then
        echo "[ERROR] Service $service_name is running on node '$service_node', but this script is running on '$current_node'" >&2
        echo "[ERROR] Cannot copy files across nodes. Please run this script on the correct node:" >&2
        echo "[ERROR]   ssh $service_node" >&2
        echo "[ERROR]   cd $(pwd)" >&2
        echo "[ERROR]   $0" >&2
        return 1
    fi
    
    echo "[ERROR] Service $service_name should be local but container not found" >&2
    return 1
}

# Function to wait for a Docker Swarm service to be ready
wait_for_service() {
    local service_name="$1"
    local max_attempts="${2:-30}"
    local attempt=1
    
    echo "[INFO] Waiting for service $service_name to be ready..." >&2
    
    while [ $attempt -le $max_attempts ]; do
        local replicas=$(docker service ls --filter "name=${service_name}" --format "{{.Replicas}}" 2>/dev/null)
        if [[ "$replicas" =~ ^[1-9][0-9]*/[1-9][0-9]*$ ]] && [[ "${replicas%/*}" == "${replicas#*/}" ]]; then
            echo "[INFO] Service $service_name is ready (replicas: $replicas)" >&2
            return 0
        else
            echo "[INFO] Attempt $attempt/$max_attempts: Service not ready (replicas: ${replicas:-'unknown'}), waiting..." >&2
            sleep 2
            attempt=$((attempt + 1))
        fi
    done
    
    echo "[ERROR] Service $service_name failed to become ready after $max_attempts attempts" >&2
    return 1
}

# Function to check if running in Docker Swarm mode
is_swarm_mode() {
    docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null | grep -q active
}

# Function to get current Docker Swarm node role
get_swarm_node_role() {
    if is_swarm_mode; then
        docker info --format '{{.Swarm.ControlAvailable}}' 2>/dev/null | grep -q true && echo "manager" || echo "worker"
    else
        echo "none"
    fi
}
