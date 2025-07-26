#!/bin/bash
# Environment Variables Validation Script
# Validates that all required environment variables are set before deployment

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

# Required environment variables for staging deployment
REQUIRED_VARS=(
    "STAGING_DB_PASSWORD"
    "PROD_DB_PASSWORD"
    "TEST_SUPERADMIN_PASSWORD"
    "TEST_ADMIN_PASSWORD"
    "TEST_USER_PASSWORD"
)

# Optional environment variables (with their defaults)
OPTIONAL_VARS=(
    "STAGING_DB_HOST:localhost"
    "STAGING_DB_PORT:5433"
    "STAGING_DB_NAME:trendsearth_staging"
    "STAGING_DB_USER:trendsearth_staging"
    "PROD_DB_HOST:localhost"
    "PROD_DB_PORT:5432"
    "PROD_DB_NAME:trendsearth"
    "PROD_DB_USER:trendsearth"
    "TEST_SUPERADMIN_EMAIL:test-superadmin@example.com"
    "TEST_ADMIN_EMAIL:test-admin@example.com"
    "TEST_USER_EMAIL:test-user@example.com"
    "DOCKER_REGISTRY:registry.example.com:5000"
)

# Function to check required variables
check_required_vars() {
    local missing_vars=()
    
    print_status "Checking required environment variables..."
    
    for var in "${REQUIRED_VARS[@]}"; do
        if [[ -z "${!var}" ]]; then
            missing_vars+=("$var")
            print_error "Missing: $var"
        else
            print_success "Found: $var"
        fi
    done
    
    if [[ ${#missing_vars[@]} -gt 0 ]]; then
        print_error "Missing required environment variables: ${missing_vars[*]}"
        print_error "Please set these variables before running deployment scripts."
        return 1
    fi
    
    print_success "All required environment variables are set!"
}

# Function to check optional variables
check_optional_vars() {
    print_status "Checking optional environment variables (showing current or default values)..."
    
    for var_default in "${OPTIONAL_VARS[@]}"; do
        local var="${var_default%:*}"
        local default="${var_default#*:}"
        local current_value="${!var:-$default}"
        
        if [[ -n "${!var}" ]]; then
            print_status "$var = $current_value (custom)"
        else
            print_status "$var = $current_value (default)"
        fi
    done
}

# Function to generate example environment file
generate_example_env() {
    local env_file="deployment-env-example.sh"
    
    print_status "Generating example environment file: $env_file"
    
    cat > "$env_file" << 'EOF'
#!/bin/bash
# Example Environment Variables for Deployment Scripts
# Copy this file and set your actual values

# Required Variables (MUST be set)
export STAGING_DB_PASSWORD="your-secure-staging-password"
export PROD_DB_PASSWORD="your-secure-production-password"
export TEST_SUPERADMIN_PASSWORD="your-secure-superadmin-password"
export TEST_ADMIN_PASSWORD="your-secure-admin-password"
export TEST_USER_PASSWORD="your-secure-user-password"

# Optional Variables (uncomment and modify if needed)
# export STAGING_DB_HOST="localhost"
# export STAGING_DB_PORT="5433"
# export STAGING_DB_NAME="trendsearth_staging"
# export STAGING_DB_USER="trendsearth_staging"
# export PROD_DB_HOST="your-production-host"
# export PROD_DB_PORT="5432"
# export PROD_DB_NAME="trendsearth"
# export PROD_DB_USER="trendsearth"
# export TEST_SUPERADMIN_EMAIL="superadmin@your-company.com"
# export TEST_ADMIN_EMAIL="admin@your-company.com"
# export TEST_USER_EMAIL="user@your-company.com"
# export DOCKER_REGISTRY="your-registry.company.com:5000"

echo "Environment variables loaded for deployment"
EOF

    chmod +x "$env_file"
    print_success "Example environment file created: $env_file"
    print_status "Edit this file with your values and run: source $env_file"
}

# Main function
main() {
    print_status "Deployment Environment Validation"
    print_status "=================================="
    
    case "${1:-check}" in
        "check")
            check_required_vars
            echo
            check_optional_vars
            ;;
        "example")
            generate_example_env
            ;;
        "help"|"--help"|"-h")
            echo "Usage: $0 [command]"
            echo ""
            echo "Commands:"
            echo "  check     Check environment variables (default)"
            echo "  example   Generate example environment file"
            echo "  help      Show this help message"
            ;;
        *)
            print_error "Unknown command: $1"
            echo "Run '$0 help' for usage information"
            exit 1
            ;;
    esac
}

main "$@"
