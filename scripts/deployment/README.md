# Deployment Scripts Reference

This directory contains modular deployment scripts that are used by the GitHub Actions workflows to set up and manage the staging environment.

## 🔒 Security Notice

**Important**: All deployment scripts have been hardened for security:

- **No hardcoded passwords**: All scripts require environment variables for sensitive data
- **No default credentials**: Scripts will fail if required passwords are not provided  
- **Private network anonymization**: Internal IP addresses replaced with examples
- **Secure examples**: Documentation uses placeholder domains and secure password examples

**Required Environment Variables** (no defaults provided):
- `STAGING_DB_PASSWORD` - Staging database password
- `PROD_DB_PASSWORD` - Production database password (for data migration)
- `TEST_SUPERADMIN_PASSWORD` - Test superadmin user password
- `TEST_ADMIN_PASSWORD` - Test admin user password  
- `TEST_USER_PASSWORD` - Test user password

**Optional Environment Variables** (have safe defaults):
- `DOCKER_REGISTRY` - Docker registry URL (defaults to `registry.example.com:5000`)
- Database connection settings (host, port, user, database names)
- Test user email addresses (default to `@example.com` domain)

## 📁 Script Overview

### Core Deployment Scripts

| Script | Purpose | Dependencies |
|--------|---------|-------------|
| `staging-postgres-container.sh` | Creates and configures PostgreSQL container | Docker, psql |
| `staging-data-migration.sh` | Runs migrations, creates users, migrates scripts | Python3, psycopg2-binary, werkzeug |
| `run-integration-tests.sh` | Comprehensive API and authentication testing | curl, jq |
| `setup-staging-users.py` | Creates test users with proper password hashing | psycopg2-binary, werkzeug |
| `migrate-production-scripts.py` | Copies recent scripts from production | psycopg2-binary |
| `validate-environment.sh` | Validates required environment variables | bash |

### Legacy Scripts (maintained for compatibility)

| Script | Purpose | Status |
|--------|---------|--------|
| `setup-docker-swarm.sh` | Full Docker Swarm initialization | ✅ Active |
| `setup-github-secrets.sh` | GitHub secrets configuration helper | ✅ Active |
| `test-deployment.sh` | Deployment testing and validation | ✅ Active |
| `staging-database-init.sh` | Legacy database setup (bash-only) | ⚠️ Deprecated |

## 🚀 Usage Patterns

### Automated Usage (GitHub Actions)

The scripts are automatically called by the staging workflow in this order:

1. **Database Setup**: `staging-postgres-container.sh`
   - Creates PostgreSQL container
   - Waits for database readiness
   - Installs Python dependencies

2. **Application Deployment**: Standard Docker Swarm deployment

3. **Data Setup**: `staging-data-migration.sh`
   - Runs database migrations
   - Creates test users
   - Migrates production scripts

4. **Integration Testing**: `run-integration-tests.sh`
   - Tests API health
   - Validates user authentication
   - Verifies permissions and data

### Manual Usage

All scripts can be run manually for debugging or local setup:

```bash
```bash
# Setup database (requires environment variables)
export STAGING_DB_PASSWORD="your-secure-password"
./scripts/deployment/staging-postgres-container.sh

# Setup staging data (requires user credentials)
export TEST_SUPERADMIN_EMAIL="admin@example.com"
export TEST_SUPERADMIN_PASSWORD="your-secure-password"
export TEST_ADMIN_PASSWORD="your-secure-password"
export TEST_USER_PASSWORD="your-secure-password"
# ... (other required test user vars)
./scripts/deployment/staging-data-migration.sh

# Run integration tests
./scripts/deployment/run-integration-tests.sh
```

## 🔧 Environment Variables

### Required for Database Setup

```bash
# Database connection (with defaults)
STAGING_DB_HOST=localhost           # Default: localhost
STAGING_DB_PORT=5433               # Default: 5433
STAGING_DB_NAME=trendsearth_staging # Default: trendsearth_staging
STAGING_DB_USER=trendsearth_staging # Default: trendsearth_staging
STAGING_DB_PASSWORD=               # REQUIRED: No default
```

### Required for Data Setup

```bash
# Test user credentials (all required)
TEST_SUPERADMIN_EMAIL=             # REQUIRED: No default
TEST_ADMIN_EMAIL=                  # REQUIRED: No default  
TEST_USER_EMAIL=                   # REQUIRED: No default
TEST_SUPERADMIN_PASSWORD=          # REQUIRED: No default
TEST_ADMIN_PASSWORD=               # REQUIRED: No default
TEST_USER_PASSWORD=                # REQUIRED: No default
```

### Optional for Production Data Migration

```bash
# Production database (optional)
PROD_DB_HOST=                      # Optional: Skip migration if not set
PROD_DB_PORT=5432                  # Default: 5432
PROD_DB_NAME=trendsearth           # Default: trendsearth
PROD_DB_USER=                      # Optional: Required if PROD_DB_HOST set
PROD_DB_PASSWORD=                  # Optional: Required if PROD_DB_HOST set
```

## 📋 Script Details

### staging-postgres-container.sh

**Purpose**: Creates and configures the PostgreSQL database container for staging.

**Key Features**:
- Environment variable validation
- Automatic dependency installation
- Container lifecycle management
- Database readiness checks
- Colored output for clear status reporting

**Exit Codes**:
- `0`: Success
- `1`: Missing required environment variables
- `1`: Database failed to become ready

**Example Output**:
```
[INFO] 🗄️ Setting up staging database...
[INFO] Installing required Python packages...
[SUCCESS] Dependencies installed
[INFO] Creating PostgreSQL container...
[SUCCESS] Database container created
[INFO] Waiting for database to be ready...
[SUCCESS] Database is ready
[SUCCESS] ✅ Staging database setup completed!
```

### staging-data-migration.sh

**Purpose**: Orchestrates data setup including migrations, user creation, and script migration.

**Key Features**:
- Database migration execution
- Test user creation with proper validation
- Optional production script migration
- Comprehensive error handling
- Summary reporting

**Dependencies**:
- `setup-staging-users.py`
- `migrate-production-scripts.py`

**Example Output**:
```
[INFO] 📊 Setting up staging data...
[INFO] 🔄 Running database migrations...
[SUCCESS] Database migrations completed
[INFO] 👥 Creating test users...
[SUCCESS] Test users created
[INFO] 📜 Migrating recent scripts from production...
[SUCCESS] Script migration completed
[SUCCESS] 📊 Staging data setup summary:
[INFO]   Test Users:
[INFO]     Superadmin: test-superadmin@example.com
[INFO]     Admin: test-admin@example.com
[INFO]     User: test-user@example.com
[INFO]   Production scripts migrated from: prod-server
[SUCCESS] ✅ Staging data setup completed!
```

### run-integration-tests.sh

**Purpose**: Comprehensive testing of the staging environment API and authentication.

**Key Features**:
- API health checks
- Multi-role authentication testing
- Permission verification
- Database content validation
- Detailed test result reporting

**Test Categories**:
1. **Health Tests**: API availability
2. **Authentication Tests**: Login for all user roles
3. **Authorization Tests**: Role-based access control
4. **Data Tests**: Database content verification

**Example Output**:
```
[INFO] 🧪 Running staging integration tests...
[INFO] Testing health endpoint...
[SUCCESS] ✅ Health endpoint working
[INFO] Testing superadmin authentication...
[SUCCESS] ✅ superadmin authentication successful
[INFO] Testing superadmin user list access...
[SUCCESS] ✅ superadmin user list access working
[INFO] 🧪 Integration Test Summary:
[INFO] ================================
[INFO]   PASS: Health endpoint
[INFO]   PASS: superadmin authentication
[INFO]   PASS: superadmin user list access
[INFO]   PASS: admin authentication
[INFO]   PASS: user authentication
[INFO]   INFO: Scripts: 42, Users: 5
[INFO] ================================
[INFO] Total: 6 | Passed: 5 | Failed: 0 | Skipped: 1
[SUCCESS] ✅ All tests passed
```

### setup-staging-users.py

**Purpose**: Creates test users with properly hashed passwords using the same methods as the application.

**Key Features**:
- Werkzeug password hashing (matches application)
- UUID generation for user IDs
- Conflict resolution (upsert logic)
- Proper database connection handling
- Detailed logging

**User Roles Created**:
- `SUPERADMIN`: Full access to all API endpoints
- `ADMIN`: User management and administrative functions
- `USER`: Basic user access and profile management

### migrate-production-scripts.py

**Purpose**: Copies scripts created or updated within the past year from production to staging.

**Key Features**:
- Date-based filtering (past year)
- Conflict resolution for duplicate slugs
- Ownership transfer to test superadmin
- Upsert logic for existing scripts
- Migration statistics reporting

**Migration Criteria**:
- Scripts with `created_at >= (now - 1 year)`
- Scripts with `updated_at >= (now - 1 year)`

## 🛡️ Environment Validation

Before running any deployment scripts, use the validation script to ensure all required environment variables are properly set:

```bash
# Check if all required variables are set
./scripts/deployment/validate-environment.sh check

# Generate an example environment file to customize
./scripts/deployment/validate-environment.sh example

# Edit the generated file with your values
vi deployment-env-example.sh

# Load the environment variables
source deployment-env-example.sh
```

The validation script will:
- ✅ Check all required environment variables are set
- ℹ️ Show current values for optional variables (without exposing sensitive data)
- 📝 Generate example environment files for easy setup

## 🔍 Troubleshooting

### Common Issues

#### Database Connection Failed
```bash
# Check if container is running
docker ps | grep trends-earth-staging-postgres

# Check logs
docker logs trends-earth-staging-postgres

# Test connection manually
PGPASSWORD="password" psql -h localhost -p 5433 -U user -d database -c "SELECT 1"
```

#### User Creation Failed
```bash
# Check environment variables are set
env | grep TEST_

# Verify database connectivity
python3 -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5433, database='db', user='user', password='pass')
print('Connected successfully')
"
```

#### Script Migration Failed
```bash
# Check production database connectivity
PGPASSWORD="prod-pass" psql -h prod-host -p 5432 -U prod-user -d prod-db -c "SELECT COUNT(*) FROM script"

# Check for recent scripts
psql -c "SELECT COUNT(*) FROM script WHERE created_at >= NOW() - INTERVAL '1 year'"
```

#### Integration Tests Failed
```bash
# Check API health manually
curl -f http://localhost:3002/api-health

# Test authentication manually
curl -X POST http://localhost:3002/auth \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password"}'

# Check if jq is installed
which jq || sudo apt-get install jq
```

### Debug Mode

Enable verbose output for all scripts:

```bash
# Run with debug output
bash -x ./scripts/deployment/staging-postgres-container.sh

# Or add debug flag to individual scripts
export DEBUG=1
export STAGING_DB_PASSWORD="your-secure-password"
./scripts/deployment/staging-data-migration.sh
```

### Log Locations

- **Docker containers**: `docker logs container-name`
- **Script output**: Captured in GitHub Actions logs
- **Database logs**: Check PostgreSQL container logs
- **Application logs**: Check manager service logs

## 🔒 Security Considerations

### Password Management
- No default passwords in scripts
- All passwords must be provided via environment variables
- Passwords are properly hashed using Werkzeug
- Test passwords should be different from production

### Database Security
- Staging database isolated from production
- Connection credentials stored as secrets
- Database accessible only within Docker network
- Regular cleanup of staging data recommended

### Script Permissions
- Scripts require execution permissions (`chmod +x`)
- Validate all inputs and environment variables
- Use `set -e` for fail-fast behavior
- Proper error handling and cleanup

## 📈 Performance Considerations

### Resource Usage
- PostgreSQL: ~200MB RAM, 0.25 CPU
- Python scripts: Minimal overhead
- Network: Depends on production data size

### Optimization Tips
- Limit script migration timeframe if too much data
- Use connection pooling for large data migrations
- Consider parallel processing for bulk operations
- Monitor staging resource usage

## 🔄 Maintenance

### Regular Tasks
- Update script dependencies
- Review and update test user credentials
- Clean up old staging data
- Monitor script execution times

### Version Updates
- Scripts are versioned with the repository
- Changes should be tested locally first
- Consider backward compatibility
- Document breaking changes

### Monitoring
- Check GitHub Actions workflow success rates
- Monitor staging environment health
- Review integration test results
- Track resource usage trends

This modular approach provides better maintainability, debugging capabilities, and reusability while keeping the GitHub Actions workflow clean and focused.
