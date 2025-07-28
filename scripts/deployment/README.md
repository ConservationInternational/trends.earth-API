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

| Script | Purpose | Dependencies | Used By |
|--------|---------|-------------|---------|
| `run-integration-tests.sh` | Comprehensive API and authentication testing | curl, jq | **GitHub Actions Workflow** |

### Container-Based Setup (Automated)

| Component | Purpose | Dependencies | Used By |
|-----------|---------|-------------|---------|
| `migrate` service | Database migrations and staging environment setup | Docker container with Python/PostgreSQL | **Docker Compose/Swarm** |
| `setup_staging_environment.py` | Complete staging setup (users, data import, verification) | psycopg2-binary, werkzeug | Called by migrate service |

### Alternative/Manual Scripts

| Script | Purpose | Dependencies | Used By |
|--------|---------|-------------|---------|
| `staging-postgres-container.sh` | Creates PostgreSQL container only | Docker, psql | Manual/debugging setup |
| `setup-staging-users.py` | Creates test users with proper password hashing | psycopg2-binary, werkzeug | ~~Legacy - now handled by migrate service~~ |
| `migrate-production-scripts.py` | Copies recent scripts from production | psycopg2-binary | ~~Legacy - now handled by migrate service~~ |
| `validate-environment.sh` | Validates required environment variables | bash | Manual validation |

## 🚀 Usage Patterns

### Automated Usage (GitHub Actions & Docker)

The staging environment setup is now fully automated:

1. **Docker Service Deployment**: Standard Docker Compose/Swarm deployment
   - PostgreSQL container starts automatically
   - Database is created automatically via `POSTGRES_DB` environment variable

2. **Migrate Service**: Runs automatically as part of deployment
   - Applies database migrations
   - Executes comprehensive staging environment setup
   - Creates properly configured test users (SUPERADMIN, ADMIN, USER)
   - Imports recent scripts from production database (past year)
   - Updates script ownership to test users
   - Provides detailed verification and reporting

3. **Integration Testing**: `run-integration-tests.sh`
   - Tests API health
   - Validates user authentication
   - Verifies permissions and data

### Container-Based Setup (Current Architecture)

All database operations now run inside Docker containers where network access and dependencies are guaranteed:

**Automatic Setup Process:**
- ✅ Database creation (handled by PostgreSQL container)
- ✅ Schema migrations (handled by migrate service)
- ✅ User creation (handled by migrate service)
- ✅ Production data import (handled by migrate service)
- ✅ Setup verification (handled by migrate service)

### Legacy Manual Usage (Deprecated)

The following manual setup is no longer needed but retained for debugging:

**Primary Method (Automated - Recommended)**:

```bash
# Deploy the staging environment - everything happens automatically
docker stack deploy -c docker-compose.staging.yml trends-earth-staging

# Monitor the automated setup progress
docker service logs trends-earth-staging_migrate

# Run integration tests after setup completes
./scripts/deployment/run-integration-tests.sh
```

**Alternative Manual Method (For Debugging Only)**:

```bash
# Setup database container only (if needed for debugging)
export STAGING_DB_PASSWORD="your-secure-password"
./scripts/deployment/staging-postgres-container.sh

# Manual user creation (if needed for debugging)
export TEST_SUPERADMIN_EMAIL="admin@example.com"
export TEST_SUPERADMIN_PASSWORD="your-secure-password"
export TEST_ADMIN_PASSWORD="your-secure-password"
export TEST_USER_PASSWORD="your-secure-password"
python3 scripts/deployment/setup-staging-users.py
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

### Migrate Service (Automated Setup)

**Purpose**: Fully automated staging environment setup that runs inside Docker containers.

**Key Features**:
- Runs database migrations automatically
- Creates test users with proper roles and password hashing
- Imports recent scripts from production database (past year)
- Updates script ownership to test superadmin user
- Comprehensive verification and reporting
- Handles all database operations from inside containers where network access is guaranteed

**Docker Service Configuration**:
The migrate service is configured in `docker-compose.staging.yml` and runs automatically when the stack is deployed.

**Environment Variables** (set in `staging.env`):
```bash
# Database connection (automatically configured)
DATABASE_URL=postgresql://trendsearth_staging:postgres@postgres:5432/trendsearth_staging

# Test user credentials (passed from GitHub secrets)
TEST_SUPERADMIN_EMAIL=test-superadmin@example.com
TEST_ADMIN_EMAIL=test-admin@example.com
TEST_USER_EMAIL=test-user@example.com
TEST_SUPERADMIN_PASSWORD=your-secure-password
TEST_ADMIN_PASSWORD=your-secure-password
TEST_USER_PASSWORD=your-secure-password

# Production database (for data import)
PROD_DB_HOST=your-prod-host
PROD_DB_PORT=5432
PROD_DB_NAME=trendsearth
PROD_DB_USER=your-prod-user
PROD_DB_PASSWORD=your-prod-password
```

**Usage**:
```bash
# Deploy the stack - migrate service runs automatically
docker stack deploy -c docker-compose.staging.yml trends-earth-staging

# Monitor progress
docker service logs trends-earth-staging_migrate
```

**vs. Modular Approach**: This script provides a comprehensive single-step setup. For more granular control, you can use individual components like `staging-postgres-container.sh` for container setup only.

### staging-postgres-container.sh (Alternative)

**Purpose**: Creates and configures the PostgreSQL database container for staging (container setup only).

**Key Features**:
- Environment variable validation
- Automatic dependency installation
- Container lifecycle management
- Database readiness checks
- Colored output for clear status reporting

**Use Case**: Use this for debugging or when you want to set up the database container separately from data population.

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
# Run with debug output for manual container setup
bash -x ./scripts/deployment/staging-postgres-container.sh

# Monitor automated setup progress
docker service logs trends-earth-staging_migrate

# Check migrate service with debug verbosity
docker service logs --details trends-earth-staging_migrate
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
