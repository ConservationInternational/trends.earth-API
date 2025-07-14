#!/bin/bash
# Staging Database Setup Script
# Sets up PostgreSQL database container for staging environment

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

# Validate required environment variables
validate_environment() {
    local required_vars=(
        "STAGING_DB_HOST"
        "STAGING_DB_PORT" 
        "STAGING_DB_NAME"
        "STAGING_DB_USER"
        "STAGING_DB_PASSWORD"
    )
    
    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            print_error "Required environment variable $var is not set"
            exit 1
        fi
    done
}

# Install required dependencies
install_dependencies() {
    print_status "Installing required Python packages..."
    python3 -m pip install --user psycopg2-binary werkzeug --quiet
    print_success "Dependencies installed"
}

# Setup PostgreSQL database container
setup_database_container() {
    print_status "Setting up PostgreSQL database container..."
    
    # Stop and remove existing container if it exists
    if docker ps -a --format '{{.Names}}' | grep -q "trends-earth-staging-postgres"; then
        print_status "Removing existing database container..."
        docker stop trends-earth-staging-postgres || true
        docker rm trends-earth-staging-postgres || true
    fi
    
    # Create Docker network if it doesn't exist
    print_status "Creating Docker network..."
    docker network create trends-earth-staging_backend --driver overlay || true
    
    # Create PostgreSQL container
    print_status "Creating PostgreSQL container..."
    docker run -d \
        --name trends-earth-staging-postgres \
        --network trends-earth-staging_backend \
        -e POSTGRES_DB="$STAGING_DB_NAME" \
        -e POSTGRES_USER="$STAGING_DB_USER" \
        -e POSTGRES_PASSWORD="$STAGING_DB_PASSWORD" \
        -p "$STAGING_DB_PORT:5432" \
        postgres:13
    
    print_success "Database container created"
}

# Wait for database to be ready
wait_for_database() {
    print_status "Waiting for database to be ready..."
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if PGPASSWORD="$STAGING_DB_PASSWORD" psql -h "$STAGING_DB_HOST" -p "$STAGING_DB_PORT" -U "$STAGING_DB_USER" -d "$STAGING_DB_NAME" -c "SELECT 1" >/dev/null 2>&1; then
            print_success "Database is ready"
            return 0
        fi
        print_status "Attempt $attempt/$max_attempts: Database not ready, waiting..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    print_error "Database failed to become ready after $max_attempts attempts"
    exit 1
}

# Main execution
main() {
    print_status "🗄️ Setting up staging database..."
    
    validate_environment
    install_dependencies
    setup_database_container
    wait_for_database
    
    print_success "✅ Staging database setup completed!"
}

# Run main function
main "$@"
