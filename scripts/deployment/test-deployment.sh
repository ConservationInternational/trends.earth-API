#!/bin/bash
# Deployment Test Script for Trends.Earth API
# Tests various deployment scenarios and validates configuration

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

# Function to check Docker Swarm status
check_swarm_status() {
    print_status "Checking Docker Swarm status..."
    
    if ! docker info | grep -q "Swarm: active"; then
        print_error "Docker Swarm is not initialized"
        return 1
    fi
    
    print_success "Docker Swarm is active"
    
    # Check node status
    print_status "Node status:"
    docker node ls
}

# Function to check environment files
check_env_files() {
    local env_type=$1
    local env_file="${env_type}.env"
    
    print_status "Checking environment file: $env_file"
    
    if [[ ! -f "$env_file" ]]; then
        print_warning "Environment file $env_file not found"
        return 1
    fi
    
    # Check for required variables
    local required_vars=("DATABASE_URL" "REDIS_URL" "JWT_SECRET_KEY")
    local missing_vars=()
    
    for var in "${required_vars[@]}"; do
        if ! grep -q "^${var}=" "$env_file"; then
            missing_vars+=("$var")
        fi
    done
    
    if [[ ${#missing_vars[@]} -gt 0 ]]; then
        print_error "Missing required variables in $env_file: ${missing_vars[*]}"
        return 1
    fi
    
    print_success "Environment file $env_file is valid"
    
    # Show variable count (without exposing values)
    local var_count=$(grep -c "^[A-Z_]*=" "$env_file" 2>/dev/null || echo "0")
    print_status "Found $var_count environment variables"
}

# Function to test Docker registry connectivity
test_registry_connection() {
    print_status "Testing Docker registry connectivity..."
    
    local registry_url="${DOCKER_REGISTRY:-registry.example.com:5000}"
    
    if [[ "$registry_url" == "registry.example.com:5000" ]]; then
        print_warning "Using default registry URL. Set DOCKER_REGISTRY environment variable for actual testing."
    fi
    
    # Test registry ping
    if curl -f "http://${registry_url}/v2/" > /dev/null 2>&1; then
        print_success "Registry is accessible: $registry_url"
    else
        print_warning "Cannot access registry: $registry_url"
        print_status "This might be expected if registry requires authentication"
    fi
}

# Function to validate compose files
validate_compose_files() {
    local compose_file=$1
    
    print_status "Validating Docker Compose file: $compose_file"
    
    if [[ ! -f "$compose_file" ]]; then
        print_error "Compose file not found: $compose_file"
        return 1
    fi
    
    # Validate compose file syntax
    if docker-compose -f "$compose_file" config > /dev/null 2>&1; then
        print_success "Compose file syntax is valid: $compose_file"
    else
        print_error "Compose file has syntax errors: $compose_file"
        return 1
    fi
    
    # Check for required services
    local services=$(docker-compose -f "$compose_file" config --services)
    print_status "Services defined: $services"
    
    # Verify required services exist
    if echo "$services" | grep -q "manager"; then
        print_success "Manager service found"
    else
        print_error "Manager service not found in $compose_file"
        return 1
    fi
}

# Function to test stack deployment (dry run)
test_stack_deployment() {
    local compose_file=$1
    local stack_name=$2
    
    print_status "Testing stack deployment (dry run): $stack_name"
    
    # Check if stack already exists
    if docker stack ls | grep -q "$stack_name"; then
        print_warning "Stack $stack_name already exists"
        print_status "Current stack services:"
        docker service ls | grep "$stack_name" || echo "No services found"
    fi
    
    # Validate that we can parse the compose file for deployment
    export DOCKER_GROUP_ID=$(getent group docker | cut -d: -f3 2>/dev/null || echo "999")
    
    if docker stack config -c "$compose_file" > /dev/null 2>&1; then
        print_success "Stack configuration is valid for: $stack_name"
    else
        print_error "Stack configuration failed for: $stack_name"
        return 1
    fi
}

# Function to check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed"
        return 1
    fi
    
    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose is not installed"
        return 1
    fi
    
    # Check Git
    if ! command -v git &> /dev/null; then
        print_error "Git is not installed"
        return 1
    fi
    
    # Check curl
    if ! command -v curl &> /dev/null; then
        print_error "curl is not installed"
        return 1
    fi
    
    print_success "All prerequisites are installed"
}

# Function to test API health endpoint
test_health_endpoint() {
    local port=$1
    local env_name=$2
    
    print_status "Testing health endpoint for $env_name (port $port)..."
    
    if curl -f "http://localhost:$port/api-health" > /dev/null 2>&1; then
        print_success "$env_name health endpoint is responding"
        
        # Show health response
        local health_response=$(curl -s "http://localhost:$port/api-health")
        echo "Health response: $health_response"
    else
        print_warning "$env_name health endpoint is not responding (port $port)"
        print_status "This is expected if the service is not running"
    fi
}

# Function to show system resources
show_system_resources() {
    print_status "System resources:"
    
    echo "Disk usage:"
    df -h | head -2
    
    echo "Memory usage:"
    free -h
    
    echo "Docker system info:"
    docker system df 2>/dev/null || echo "Docker system df not available"
}

# Function to run comprehensive tests
run_tests() {
    print_status "Running deployment tests..."
    print_status "==============================="
    
    # Prerequisites
    check_prerequisites || exit 1
    
    # System resources
    show_system_resources
    
    # Docker Swarm
    check_swarm_status || exit 1
    
    # Environment files
    check_env_files "prod" || print_warning "Production environment file issues"
    check_env_files "staging" || print_warning "Staging environment file issues"
    
    # Docker registry
    test_registry_connection
    
    # Compose files
    validate_compose_files "docker-compose.prod.yml" || exit 1
    validate_compose_files "docker-compose.staging.yml" || exit 1
    
    # Stack deployment tests
    test_stack_deployment "docker-compose.prod.yml" "trends-earth-prod"
    test_stack_deployment "docker-compose.staging.yml" "trends-earth-staging"
    
    # Health endpoints (if services are running)
    test_health_endpoint "3001" "Production"
    test_health_endpoint "3002" "Staging"
    
    print_success "Deployment tests completed!"
    print_status "==============================="
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --help              Show this help message"
    echo "  --check-swarm       Check Docker Swarm status only"
    echo "  --check-env         Check environment files only"
    echo "  --check-compose     Check compose files only"
    echo "  --check-health      Check health endpoints only"
    echo "  --test-all          Run all tests (default)"
    echo ""
    echo "Examples:"
    echo "  $0                  # Run all tests"
    echo "  $0 --check-swarm    # Check Docker Swarm only"
    echo "  $0 --check-health   # Check health endpoints only"
}

# Main execution
main() {
    case "${1:-}" in
        --help)
            show_usage
            exit 0
            ;;
        --check-swarm)
            check_swarm_status
            ;;
        --check-env)
            check_env_files "prod"
            check_env_files "staging"
            ;;
        --check-compose)
            validate_compose_files "docker-compose.prod.yml"
            validate_compose_files "docker-compose.staging.yml"
            ;;
        --check-health)
            test_health_endpoint "3001" "Production"
            test_health_endpoint "3002" "Staging"
            ;;
        --test-all|"")
            run_tests
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"
