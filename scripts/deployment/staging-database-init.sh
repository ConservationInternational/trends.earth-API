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
    
    # Export recent scripts using INSERT statements to avoid CSV formatting issues
    temp_insert_file="/tmp/staging_scripts_insert.sql"
    
    PGPASSWORD="$PROD_DB_PASSWORD" psql \
        -h "$PROD_DB_HOST" \
        -p "$PROD_DB_PORT" \
        -U "$PROD_DB_USER" \
        -d "$PROD_DB_NAME" \
        -t -A \
        -c "
        SELECT 
            'INSERT INTO script (id, name, slug, description, created_at, updated_at, user_id, status, public, cpu_reservation, cpu_limit, memory_reservation, memory_limit, environment, environment_version) VALUES (' ||
            QUOTE_LITERAL(id) || ', ' ||
            QUOTE_LITERAL(COALESCE(name, '')) || ', ' ||
            QUOTE_LITERAL(COALESCE(slug, '')) || ', ' ||
            QUOTE_LITERAL(COALESCE(description, '')) || ', ' ||
            QUOTE_LITERAL(created_at::text) || ', ' ||
            QUOTE_LITERAL(updated_at::text) || ', ' ||
            QUOTE_LITERAL('$superadmin_id') || ', ' ||
            QUOTE_LITERAL(COALESCE(status, 'PENDING')) || ', ' ||
            COALESCE(public::text, 'false') || ', ' ||
            COALESCE(cpu_reservation::text, '0') || ', ' ||
            COALESCE(cpu_limit::text, '0') || ', ' ||
            COALESCE(memory_reservation::text, '0') || ', ' ||
            COALESCE(memory_limit::text, '0') || ', ' ||
            QUOTE_LITERAL(COALESCE(environment, '')) || ', ' ||
            QUOTE_LITERAL(COALESCE(environment_version, '')) ||
            ') ON CONFLICT (id) DO NOTHING;'
        FROM script 
        WHERE (created_at >= '$one_year_ago' OR updated_at >= '$one_year_ago');" \
        > "$temp_insert_file"
    
    if [ -s "$temp_insert_file" ]; then
        print_status "Importing recent scripts into staging database..."
        
        # Execute the INSERT statements
        PGPASSWORD="$STAGING_DB_PASSWORD" psql \
            -h "$STAGING_DB_HOST" \
            -p "$STAGING_DB_PORT" \
            -U "$STAGING_DB_USER" \
            -d "$STAGING_DB_NAME" \
            -f "$temp_insert_file"
        
        print_success "Recent scripts imported successfully"
    else
        print_warning "No recent scripts found to import"
    fi
    
    # Clean up temporary files
    rm -f "$temp_script_file" "$temp_insert_file"
}

# Function to copy recent status logs from production
copy_recent_status_logs() {
    print_status "Copying recent status logs from production database..."
    
    # Calculate date for last month
    one_month_ago=$(date -d "1 month ago" '+%Y-%m-%d %H:%M:%S')
    print_status "Copying status logs from: $one_month_ago"
    
    # Check if production database has status_log table
    prod_status_log_exists=$(PGPASSWORD="$PROD_DB_PASSWORD" psql \
        -h "$PROD_DB_HOST" \
        -p "$PROD_DB_PORT" \
        -U "$PROD_DB_USER" \
        -d "$PROD_DB_NAME" \
        -t -c "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'status_log');" | xargs)
    
    if [ "$prod_status_log_exists" != "t" ]; then
        print_warning "Production database does not have status_log table, skipping status logs import"
        return 0
    fi
    
    # Check if staging database has status_log table
    staging_status_log_exists=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
        -h "$STAGING_DB_HOST" \
        -p "$STAGING_DB_PORT" \
        -U "$STAGING_DB_USER" \
        -d "$STAGING_DB_NAME" \
        -t -c "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'status_log');" | xargs)
    
    if [ "$staging_status_log_exists" != "t" ]; then
        print_warning "Staging database does not have status_log table, skipping status logs import"
        return 0
    fi
    
    # Export recent status logs using INSERT statements
    temp_status_log_file="/tmp/staging_status_logs_insert.sql"
    
    PGPASSWORD="$PROD_DB_PASSWORD" psql \
        -h "$PROD_DB_HOST" \
        -p "$PROD_DB_PORT" \
        -U "$PROD_DB_USER" \
        -d "$PROD_DB_NAME" \
        -t -A \
        -c "
        SELECT 
            'INSERT INTO status_log (id, timestamp, executions_active, executions_ready, executions_running, executions_finished, executions_failed, executions_count, users_count, scripts_count, memory_available_percent, cpu_usage_percent) VALUES (' ||
            QUOTE_LITERAL(id) || ', ' ||
            QUOTE_LITERAL(timestamp::text) || ', ' ||
            COALESCE(executions_active::text, '0') || ', ' ||
            COALESCE(executions_ready::text, '0') || ', ' ||
            COALESCE(executions_running::text, '0') || ', ' ||
            COALESCE(executions_finished::text, '0') || ', ' ||
            COALESCE(executions_failed::text, '0') || ', ' ||
            COALESCE(executions_count::text, '0') || ', ' ||
            COALESCE(users_count::text, '0') || ', ' ||
            COALESCE(scripts_count::text, '0') || ', ' ||
            COALESCE(memory_available_percent::text, '0.0') || ', ' ||
            COALESCE(cpu_usage_percent::text, '0.0') ||
            ') ON CONFLICT (id) DO NOTHING;'
        FROM status_log 
        WHERE timestamp >= '$one_month_ago'
        ORDER BY timestamp DESC;" \
        > "$temp_status_log_file"
    
    if [ -s "$temp_status_log_file" ]; then
        print_status "Importing recent status logs into staging database..."
        
        # Count records being imported
        record_count=$(grep -c "INSERT INTO status_log" "$temp_status_log_file" || echo "0")
        print_status "Importing $record_count status log records..."
        
        # Execute the INSERT statements
        PGPASSWORD="$STAGING_DB_PASSWORD" psql \
            -h "$STAGING_DB_HOST" \
            -p "$STAGING_DB_PORT" \
            -U "$STAGING_DB_USER" \
            -d "$STAGING_DB_NAME" \
            -f "$temp_status_log_file"
        
        print_success "Recent status logs imported successfully ($record_count records)"
    else
        print_warning "No recent status logs found to import"
    fi
    
    # Clean up temporary files
    rm -f "$temp_status_log_file"
}

# Function to copy script logs for imported scripts
copy_script_logs_for_imported_scripts() {
    print_status "Copying script logs for imported scripts from production database..."
    
    # Check if production database has script_log table
    prod_script_log_exists=$(PGPASSWORD="$PROD_DB_PASSWORD" psql \
        -h "$PROD_DB_HOST" \
        -p "$PROD_DB_PORT" \
        -U "$PROD_DB_USER" \
        -d "$PROD_DB_NAME" \
        -t -c "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'script_log');" | xargs)
    
    if [ "$prod_script_log_exists" != "t" ]; then
        print_warning "Production database does not have script_log table, skipping script logs import"
        return 0
    fi
    
    # Check if staging database has script_log table
    staging_script_log_exists=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
        -h "$STAGING_DB_HOST" \
        -p "$STAGING_DB_PORT" \
        -U "$STAGING_DB_USER" \
        -d "$STAGING_DB_NAME" \
        -t -c "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'script_log');" | xargs)
    
    if [ "$staging_script_log_exists" != "t" ]; then
        print_warning "Staging database does not have script_log table, skipping script logs import"
        return 0
    fi
    
    # Export script logs for scripts that exist in staging using INSERT statements
    temp_script_log_file="/tmp/staging_script_logs_insert.sql"
    
    print_status "Exporting script logs for imported scripts..."
    
    PGPASSWORD="$PROD_DB_PASSWORD" psql \
        -h "$PROD_DB_HOST" \
        -p "$PROD_DB_PORT" \
        -U "$PROD_DB_USER" \
        -d "$PROD_DB_NAME" \
        -t -A \
        -c "
        SELECT 
            'INSERT INTO script_log (id, text, register_date, script_id) VALUES (' ||
            QUOTE_LITERAL(sl.id) || ', ' ||
            QUOTE_LITERAL(COALESCE(sl.text, '')) || ', ' ||
            QUOTE_LITERAL(sl.register_date::text) || ', ' ||
            QUOTE_LITERAL(sl.script_id) ||
            ') ON CONFLICT (id) DO NOTHING;'
        FROM script_log sl
        INNER JOIN script s ON sl.script_id = s.id
        WHERE (s.created_at >= '$(date -d "1 year ago" '+%Y-%m-%d')' OR s.updated_at >= '$(date -d "1 year ago" '+%Y-%m-%d')')
        ORDER BY sl.register_date DESC;" \
        > "$temp_script_log_file"
    
    if [ -s "$temp_script_log_file" ]; then
        print_status "Importing script logs for imported scripts into staging database..."
        
        # Count records being imported
        record_count=$(grep -c "INSERT INTO script_log" "$temp_script_log_file" || echo "0")
        print_status "Importing $record_count script log records..."
        
        # Execute the INSERT statements
        PGPASSWORD="$STAGING_DB_PASSWORD" psql \
            -h "$STAGING_DB_HOST" \
            -p "$STAGING_DB_PORT" \
            -U "$STAGING_DB_USER" \
            -d "$STAGING_DB_NAME" \
            -f "$temp_script_log_file"
        
        print_success "Script logs for imported scripts imported successfully ($record_count records)"
    else
        print_warning "No script logs found for imported scripts"
    fi
    
    # Clean up temporary files
    rm -f "$temp_script_log_file"
}

# Function to create test users
create_test_users() {
    print_status "Creating test users in staging database using Python script..."
    
    # Use the Python user setup script which properly hashes passwords
    # Run it inside the Docker container where all dependencies are available
    if [ -f "scripts/deployment/setup-staging-users.py" ]; then
        print_status "Running Python user setup script inside Docker container..."
        
        # Find the manager container ID
        manager_container_id=$(docker ps --filter "name=trends-earth-staging_manager" --format "{{.ID}}" | head -1)
        
        if [ -z "$manager_container_id" ]; then
            print_error "Could not find trends-earth-staging_manager container"
            exit 1
        fi
        
        # Copy the script into the container
        docker cp scripts/deployment/setup-staging-users.py "$manager_container_id:/opt/gef-api/setup-staging-users.py"
        
        # Run the script inside the container
        docker exec "$manager_container_id" python setup-staging-users.py
        
        # Clean up the copied script
        docker exec "$manager_container_id" rm -f /opt/gef-api/setup-staging-users.py
        
        print_success "Test users created successfully with proper password hashes"
        print_status "Test user credentials:"
        print_status "  Superadmin: $TEST_SUPERADMIN_EMAIL (password: $TEST_SUPERADMIN_PASSWORD)"
        print_status "  Admin: $TEST_ADMIN_EMAIL (password: $TEST_ADMIN_PASSWORD)"  
        print_status "  User: $TEST_USER_EMAIL (password: $TEST_USER_PASSWORD)"
        
        # Get the superadmin ID for script ownership updates
        superadmin_id=$(PGPASSWORD="$STAGING_DB_PASSWORD" psql \
            -h "$STAGING_DB_HOST" \
            -p "$STAGING_DB_PORT" \
            -U "$STAGING_DB_USER" \
            -d "$STAGING_DB_NAME" \
            -t -c "SELECT id FROM \"user\" WHERE email = '$TEST_SUPERADMIN_EMAIL' LIMIT 1;" | xargs)
        
        if [ -n "$superadmin_id" ] && [ "$superadmin_id" != "" ]; then
            echo "$superadmin_id" > /tmp/superadmin_id.txt
        fi
    else
        print_error "Python user setup script not found at scripts/deployment/setup-staging-users.py"
        exit 1
    fi
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
    create_test_users                      # Create users first
    copy_recent_scripts                    # Then import scripts with correct user_id
    copy_recent_status_logs                # Import recent status logs for testing
    copy_script_logs_for_imported_scripts  # Import script logs for the imported scripts
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
