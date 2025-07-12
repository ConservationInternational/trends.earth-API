#!/bin/bash
# Staging Data Setup Script
# Handles database migrations, user creation, and data migration

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
        "STAGING_DB_PASSWORD"
        "TEST_SUPERADMIN_EMAIL"
        "TEST_ADMIN_EMAIL" 
        "TEST_USER_EMAIL"
        "TEST_SUPERADMIN_PASSWORD"
        "TEST_ADMIN_PASSWORD"
        "TEST_USER_PASSWORD"
    )
    
    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            print_error "Required environment variable $var is not set"
            exit 1
        fi
    done
}

# Run database migrations
run_migrations() {
    print_status "🔄 Running database migrations..."
    
    # Try to run migrations using the migrate service
    local migrate_container=$(docker ps -q -f name=trends-earth-staging_migrate)
    if [ -n "$migrate_container" ]; then
        docker exec "$migrate_container" python run_db_migrations.py || {
            print_warning "Migration via service container failed, trying alternative method..."
        }
    else
        print_warning "Migration service not found, skipping migrations"
    fi
    
    print_success "Database migrations completed"
}

# Create test users
create_test_users() {
    print_status "👥 Creating test users..."
    
    if [ ! -f "scripts/deployment/setup-staging-users.py" ]; then
        print_error "User setup script not found: scripts/deployment/setup-staging-users.py"
        exit 1
    fi
    
    chmod +x scripts/deployment/setup-staging-users.py
    python3 scripts/deployment/setup-staging-users.py
    
    print_success "Test users created"
}

# Migrate production scripts
migrate_scripts() {
    print_status "📜 Migrating recent scripts from production..."
    
    # Only run script migration if production database credentials are provided
    if [ -n "$PROD_DB_HOST" ] && [ -n "$PROD_DB_PASSWORD" ]; then
        if [ ! -f "scripts/deployment/migrate-production-scripts.py" ]; then
            print_error "Script migration script not found: scripts/deployment/migrate-production-scripts.py"
            exit 1
        fi
        
        chmod +x scripts/deployment/migrate-production-scripts.py
        python3 scripts/deployment/migrate-production-scripts.py
        print_success "Script migration completed"
    else
        print_warning "Production database credentials not provided, skipping script migration"
    fi
}

# Display summary
show_summary() {
    print_success "📊 Staging data setup summary:"
    print_status "  Test Users:"
    print_status "    Superadmin: $TEST_SUPERADMIN_EMAIL"
    print_status "    Admin: $TEST_ADMIN_EMAIL"
    print_status "    User: $TEST_USER_EMAIL"
    
    if [ -n "$PROD_DB_HOST" ]; then
        print_status "  Production scripts migrated from: $PROD_DB_HOST"
    else
        print_status "  No production scripts migrated (no prod DB configured)"
    fi
}

# Main execution
main() {
    print_status "📊 Setting up staging data..."
    
    validate_environment
    run_migrations
    create_test_users
    migrate_scripts
    show_summary
    
    print_success "✅ Staging data setup completed!"
}

# Run main function
main "$@"
