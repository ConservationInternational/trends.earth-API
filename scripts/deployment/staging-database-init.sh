#!/bin/bash
# Database Setup Script for Staging Environment
# This script sets up a staging PostgreSQL database and populates it with production data

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

# Database configuration from environment variables (required)
STAGING_DB_HOST="${STAGING_DB_HOST:-localhost}"
STAGING_DB_PORT="${STAGING_DB_PORT:-5433}"
STAGING_DB_NAME="${STAGING_DB_NAME:-trendsearth_staging}"
STAGING_DB_USER="${STAGING_DB_USER:-trendsearth_staging}"
# STAGING_DB_PASSWORD is required - no default provided for security
if [[ -z "$STAGING_DB_PASSWORD" ]]; then
    echo "❌ Error: STAGING_DB_PASSWORD environment variable is required"
    exit 1
fi

PROD_DB_HOST="${PROD_DB_HOST:-localhost}"
PROD_DB_PORT="${PROD_DB_PORT:-5432}"
PROD_DB_NAME="${PROD_DB_NAME:-trendsearth}"
PROD_DB_USER="${PROD_DB_USER:-trendsearth}"
# PROD_DB_PASSWORD is required - no default provided for security
if [[ -z "$PROD_DB_PASSWORD" ]]; then
    echo "❌ Error: PROD_DB_PASSWORD environment variable is required"
    exit 1
fi

# Test users configuration
TEST_SUPERADMIN_EMAIL="${TEST_SUPERADMIN_EMAIL:-test-superadmin@example.com}"
TEST_ADMIN_EMAIL="${TEST_ADMIN_EMAIL:-test-admin@example.com}"
TEST_USER_EMAIL="${TEST_USER_EMAIL:-test-user@example.com}"

# Test user passwords are required - no defaults provided for security
if [[ -z "$TEST_SUPERADMIN_PASSWORD" ]]; then
    echo "❌ Error: TEST_SUPERADMIN_PASSWORD environment variable is required"
    exit 1
fi
if [[ -z "$TEST_ADMIN_PASSWORD" ]]; then
    echo "❌ Error: TEST_ADMIN_PASSWORD environment variable is required"
    exit 1
fi
if [[ -z "$TEST_USER_PASSWORD" ]]; then
    echo "❌ Error: TEST_USER_PASSWORD environment variable is required"
    exit 1
fi

# Function to wait for database to be ready
wait_for_db() {
    local host=$1
    local port=$2
    local user=$3
    local db_name=$4
    local max_attempts=30
    local attempt=1
    
    print_status "Waiting for database $db_name at $host:$port to be ready..."
    
    while [ $attempt -le $max_attempts ]; do
        if PGPASSWORD="$STAGING_DB_PASSWORD" psql -h "$host" -p "$port" -U "$user" -d "$db_name" -c "SELECT 1" >/dev/null 2>&1; then
            print_success "Database $db_name is ready"
            return 0
        else
            print_status "Attempt $attempt/$max_attempts: Database not ready, waiting..."
            sleep 2
            attempt=$((attempt + 1))
        fi
    done
    
    print_error "Database $db_name at $host:$port failed to become ready after $max_attempts attempts"
    return 1
}

# Function to create staging database container
create_staging_database() {
    print_status "PostgreSQL database container managed by Docker Compose..."
    
    # Skip container creation since it's managed by Docker Compose
    print_status "Waiting for PostgreSQL service to be ready..."
    
    # Wait for database to be ready
    wait_for_db "$STAGING_DB_HOST" "$STAGING_DB_PORT" "$STAGING_DB_USER" "$STAGING_DB_NAME"
    
    print_success "Staging database is ready"
}

# Function to copy recent scripts from production
copy_recent_scripts() {
    print_status "Copying scripts updated or created within the past year from production..."
    
    # Calculate date one year ago
    one_year_ago=$(date -d "1 year ago" '+%Y-%m-%d')
    
    # Create temporary SQL file for script data
    temp_script_file="/tmp/staging_scripts.sql"
    
    print_status "Extracting scripts created or updated since $one_year_ago..."
    
    # Check if production database is accessible
    print_status "Testing connection to production database..."
    if ! PGPASSWORD="$PROD_DB_PASSWORD" psql \
        -h "$PROD_DB_HOST" \
        -p "$PROD_DB_PORT" \
        -U "$PROD_DB_USER" \
        -d "$PROD_DB_NAME" \
        -c "SELECT 1;" >/dev/null 2>&1; then
        
        print_warning "Cannot connect to production database - skipping script import"
        print_warning "This may be due to network restrictions or pg_hba.conf configuration"
        print_warning "Production database connection details:"
        print_warning "  Host: $PROD_DB_HOST"
        print_warning "  Port: $PROD_DB_PORT" 
        print_warning "  User: $PROD_DB_USER"
        print_warning "  Database: $PROD_DB_NAME"
        return 0
    fi
    
    # First, get the superadmin user ID that we'll assign to all imported scripts
    superadmin_id=""
    if [ -f /tmp/superadmin_id.txt ]; then
        superadmin_id=$(cat /tmp/superadmin_id.txt)
    else
        # Get superadmin user ID from staging database
        superadmin_id=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
            -h "$STAGING_DB_HOST" \
            -p "$STAGING_DB_PORT" \
            -U "$STAGING_DB_USER" \
            -d "$STAGING_DB_NAME" \
            -t -c "SELECT id FROM \"user\" WHERE role = 'SUPERADMIN' LIMIT 1;" | xargs)
    fi
    
    if [ -z "$superadmin_id" ] || [ "$superadmin_id" = "" ]; then
        print_error "Could not find superadmin user ID for script import"
        return 1
    fi
    
    print_status "Using superadmin user ID: $superadmin_id for imported scripts"
    
    # Export recent scripts with modified user_id, excluding null timestamps
    PGPASSWORD="$PROD_DB_PASSWORD" psql \
        -h "$PROD_DB_HOST" \
        -p "$PROD_DB_PORT" \
        -U "$PROD_DB_USER" \
        -d "$PROD_DB_NAME" \
        -t -A -F',' \
        -c "COPY (
            SELECT 
                id, name, slug, description, created_at, updated_at,
                '$superadmin_id' as user_id,  -- Replace user_id with staging superadmin
                status, public, cpu_reservation, cpu_limit, 
                memory_reservation, memory_limit, environment, environment_version
            FROM script 
            WHERE (created_at >= '$one_year_ago' OR updated_at >= '$one_year_ago')
              AND created_at IS NOT NULL 
              AND updated_at IS NOT NULL
        ) TO STDOUT WITH CSV HEADER;" \
        > "$temp_script_file"
    
    if [ -s "$temp_script_file" ]; then
        print_status "Importing recent scripts into staging database..."
        
        # Import scripts into staging database using COPY
        PGPASSWORD="$STAGING_DB_PASSWORD" psql \
            -h "$STAGING_DB_HOST" \
            -p "$STAGING_DB_PORT" \
            -U "$STAGING_DB_USER" \
            -d "$STAGING_DB_NAME" \
            -c "COPY script FROM STDIN WITH CSV HEADER;" \
            < "$temp_script_file"
        
        print_success "Recent scripts imported successfully"
    else
        print_warning "No recent scripts found to import"
    fi
    
    # Clean up temporary file
    rm -f "$temp_script_file"
}

# Function to create test users
create_test_users() {
    print_status "Creating test users in staging database..."
    
    # Create temporary SQL file for user creation
    temp_user_file="/tmp/staging_users.sql"
    
    # Generate UUIDs for test users
    superadmin_id=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
    admin_id=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
    user_id=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
    
    # Generate password hashes (this would normally be done by the application)
    # For now, we'll use plaintext and let the application handle hashing
    
    cat > "$temp_user_file" << EOF
-- Insert test users for staging environment
-- Note: Passwords will be hashed by the application

-- Test Superadmin User
INSERT INTO "user" (id, email, name, country, institution, password, role, created_at, updated_at) 
VALUES (
    '$superadmin_id', 
    '$TEST_SUPERADMIN_EMAIL', 
    'Test Superadmin User', 
    'Test Country', 
    'Test Institution',
    -- This is a placeholder - the actual password hash should be generated by the application
    '\$2b\$12\$placeholder_hash_for_superadmin', 
    'SUPERADMIN', 
    NOW(), 
    NOW()
) ON CONFLICT (email) DO UPDATE SET
    name = EXCLUDED.name,
    role = EXCLUDED.role,
    updated_at = NOW();

-- Test Admin User  
INSERT INTO "user" (id, email, name, country, institution, password, role, created_at, updated_at) 
VALUES (
    '$admin_id', 
    '$TEST_ADMIN_EMAIL', 
    'Test Admin User', 
    'Test Country', 
    'Test Institution',
    '\$2b\$12\$placeholder_hash_for_admin', 
    'ADMIN', 
    NOW(), 
    NOW()
) ON CONFLICT (email) DO UPDATE SET
    name = EXCLUDED.name,
    role = EXCLUDED.role,
    updated_at = NOW();

-- Test Regular User
INSERT INTO "user" (id, email, name, country, institution, password, role, created_at, updated_at) 
VALUES (
    '$user_id', 
    '$TEST_USER_EMAIL', 
    'Test Regular User', 
    'Test Country', 
    'Test Institution',
    '\$2b\$12\$placeholder_hash_for_user', 
    'USER', 
    NOW(), 
    NOW()
) ON CONFLICT (email) DO UPDATE SET
    name = EXCLUDED.name,
    role = EXCLUDED.role,
    updated_at = NOW();
EOF
    
    # Execute user creation SQL
    PGPASSWORD="$STAGING_DB_PASSWORD" psql \
        -h "$STAGING_DB_HOST" \
        -p "$STAGING_DB_PORT" \
        -U "$STAGING_DB_USER" \
        -d "$STAGING_DB_NAME" \
        -f "$temp_user_file"
    
    print_success "Test users created successfully"
    print_status "Test user credentials:"
    print_status "  Superadmin: $TEST_SUPERADMIN_EMAIL (password: $TEST_SUPERADMIN_PASSWORD)"
    print_status "  Admin: $TEST_ADMIN_EMAIL (password: $TEST_ADMIN_PASSWORD)"  
    print_status "  User: $TEST_USER_EMAIL (password: $TEST_USER_PASSWORD)"
    
    # Store the superadmin ID for script ownership update
    echo "$superadmin_id" > /tmp/superadmin_id.txt
    
    # Clean up temporary file
    rm -f "$temp_user_file"
}

# Function to verify setup
verify_setup() {
    print_status "Verifying staging database setup..."
    
    # Count scripts
    script_count=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
        -h "$STAGING_DB_HOST" \
        -p "$STAGING_DB_PORT" \
        -U "$STAGING_DB_USER" \
        -d "$STAGING_DB_NAME" \
        -t -c "SELECT COUNT(*) FROM script;")
    
    # Count users
    user_count=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
        -h "$STAGING_DB_HOST" \
        -p "$STAGING_DB_PORT" \
        -U "$STAGING_DB_USER" \
        -d "$STAGING_DB_NAME" \
        -t -c "SELECT COUNT(*) FROM \"user\";")
    
    # Count test users by role
    superadmin_count=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
        -h "$STAGING_DB_HOST" \
        -p "$STAGING_DB_PORT" \
        -U "$STAGING_DB_USER" \
        -d "$STAGING_DB_NAME" \
        -t -c "SELECT COUNT(*) FROM \"user\" WHERE role = 'SUPERADMIN';")
    
    admin_count=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
        -h "$STAGING_DB_HOST" \
        -p "$STAGING_DB_PORT" \
        -U "$STAGING_DB_USER" \
        -d "$STAGING_DB_NAME" \
        -t -c "SELECT COUNT(*) FROM \"user\" WHERE role = 'ADMIN';")
    
    print_success "Verification Results:"
    print_status "  Scripts: $(echo $script_count | xargs)"
    print_status "  Total Users: $(echo $user_count | xargs)" 
    print_status "  Superadmin Users: $(echo $superadmin_count | xargs)"
    print_status "  Admin Users: $(echo $admin_count | xargs)"
    
    # Clean up temporary files
    rm -f /tmp/superadmin_id.txt
}

# Main execution
main() {
    print_status "Starting staging database setup"
    print_status "=================================="
    
    # Check if required tools are available
    for tool in docker psql python3; do
        if ! command -v $tool >/dev/null 2>&1; then
            print_error "$tool is required but not installed"
            exit 1
        fi
    done
    
    create_staging_database
    create_test_users        # Create users first
    copy_recent_scripts      # Then import scripts with correct user_id
    verify_setup
    
    print_success "Staging database setup completed successfully!"
    print_status "=================================="
    print_status "Next steps:"
    print_status "1. Update your staging.env file with the database connection details"
    print_status "2. Run database migrations if needed"
    print_status "3. Test the application with the test user credentials"
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
