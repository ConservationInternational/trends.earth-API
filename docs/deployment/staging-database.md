# Staging Database Setup Guide

## Overview

The staging deployment workflow automatically sets up a comprehensive staging environment that includes:

1. **PostgreSQL Database**: A dedicated staging database container
2. **Production Data Migration**: Copies scripts created/updated within the past year
3. **Test Users**: Creates three test users with different permission levels
4. **Script Ownership**: Updates all imported scripts to be owned by the test superadmin

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Staging Environment                      │
├─────────────────────────────────────────────────────────────┤
│ Application Services (trends-earth-staging stack)          │
│ ├── Manager (API Server) - Port 3002                       │
│ ├── Worker (Background Jobs)                               │
│ ├── Beat (Scheduler)                                        │
│ ├── Redis (Cache & Queue)                                  │
│ └── PostgreSQL (Staging Database) - Port 5433             │
├─────────────────────────────────────────────────────────────┤
│ Data Migration Process                                      │
│ ├── Copy recent scripts from production                    │
│ ├── Create test users (superadmin, admin, user)           │
│ └── Update script ownership                                 │
└─────────────────────────────────────────────────────────────┘
```

## Test Users

The staging environment automatically creates three test users:

### Test Superadmin
- **Email**: `test-superadmin@example.com` (configurable)
- **Password**: (configurable)
- **Role**: `SUPERADMIN`
- **Permissions**: Full access to all API endpoints and admin functions

### Test Admin
- **Email**: `test-admin@example.com` (configurable)
- **Password**: (configurable)
- **Role**: `ADMIN`
- **Permissions**: User management, script viewing, limited admin functions

### Test User
- **Email**: `test-user@example.com` (configurable)
- **Password**: (configurable)
- **Role**: `USER`
- **Permissions**: Basic user access, own profile management

## Configuration

### Required GitHub Secrets

#### Database Configuration
```bash
# Staging database settings
STAGING_DB_HOST=localhost
STAGING_DB_PORT=5433
STAGING_DB_NAME=trendsearth_staging
STAGING_DB_USER=trendsearth_staging
STAGING_DB_PASSWORD=your-secure-password

# Production database (for data migration)
PROD_DB_HOST=your-production-db-host
PROD_DB_PORT=5432
PROD_DB_NAME=trendsearth
PROD_DB_USER=your-prod-db-user
PROD_DB_PASSWORD=your-prod-db-password
```

#### Test User Configuration (Required)
```bash
# Test user emails (required)
TEST_SUPERADMIN_EMAIL=test-superadmin@example.com
TEST_ADMIN_EMAIL=test-admin@example.com
TEST_USER_EMAIL=test-user@example.com

# Test user passwords (required - no defaults provided for security)
TEST_SUPERADMIN_PASSWORD=your-secure-superadmin-password
TEST_ADMIN_PASSWORD=your-secure-admin-password
TEST_USER_PASSWORD=your-secure-user-password
```

**Important**: Test user passwords are now required and must be set via GitHub secrets. No default passwords are provided for security reasons.

### Environment Files

Create a `staging.env` file with your staging environment configuration:

```env
# Database Configuration
DATABASE_URL=postgresql://trendsearth_staging:password@postgres:5432/trendsearth_staging

# Application Settings
FLASK_ENV=staging
DEBUG=False
SECRET_KEY=your-staging-secret-key

# External Services
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# API Configuration
API_VERSION=v1
CORS_ORIGINS=*

# Logging
LOG_LEVEL=INFO
```

## Deployment Process

### Automatic Deployment (Recommended)

The staging workflow automatically triggers on:
- Push to `staging` branch
- Push to `develop` branch
- Manual workflow dispatch

The deployment process:

1. **Database Setup**
   - Creates PostgreSQL container
   - Waits for database to be ready
   - Installs required Python packages

2. **Application Deployment**
   - Builds and pushes Docker image
   - Deploys services via Docker Swarm
   - Performs health checks

3. **Data Migration**
   - Database migrations run automatically via migrate service
   - Creates test users with hashed passwords
   - Copies recent scripts from production
   - Updates script ownership to test superadmin

4. **Integration Testing**
   - Tests API health endpoint
   - Validates test user authentication
   - Verifies admin and user access levels
   - Checks database content

### Manual Setup

The staging database setup is now fully automated through the Docker migrate service:

```bash
# Deploy the staging stack - everything happens automatically
docker stack deploy -c docker-compose.staging.yml trends-earth-staging

# Monitor the automated setup progress
docker service logs trends-earth-staging_migrate
```

**Note**: Database migrations, user creation, and data import are all handled automatically by the `trends-earth-staging_migrate` service when the Docker stack is deployed. No manual scripts are required.

## Data Migration Details

### Script Migration Criteria

Scripts are migrated from production if they meet any of these criteria:
- `created_at` date is within the past year
- `updated_at` date is within the past year

### Migration Process

1. **Query Production**: Fetches qualifying scripts from production database
2. **Transform Data**: Updates ownership to staging superadmin user
3. **Handle Conflicts**: Resolves slug conflicts by appending timestamps
4. **Upsert Logic**: Updates existing scripts or creates new ones
5. **Verification**: Counts and reports migration results

### Script Ownership

All migrated scripts are assigned to the test superadmin user to ensure:
- Consistent ownership for testing
- Admin-level access to all scripts
- Simplified permission testing

## Testing and Validation

### Automated Tests

The workflow includes comprehensive integration tests:

```bash
# API Health Check
curl -f http://localhost:3002/api-health

# User Authentication Tests
curl -X POST http://localhost:3002/auth \
  -H "Content-Type: application/json" \
  -d '{"email":"test-superadmin@example.com","password":"your-secure-password"}'

# Authorization Tests
curl -H "Authorization: Bearer $TOKEN" http://localhost:3002/user
curl -H "Authorization: Bearer $TOKEN" http://localhost:3002/script
```

### Manual Testing

You can test the staging environment manually:

1. **Login to Staging**
   ```bash
   curl -X POST http://staging-server:3002/auth \
     -H "Content-Type: application/json" \
     -d '{"email":"test-superadmin@example.com","password":"your-secure-password"}'
   ```

2. **Test API Endpoints**
   ```bash
   # List users (admin/superadmin only)
   curl -H "Authorization: Bearer $TOKEN" http://staging-server:3002/user
   
   # List scripts
   curl -H "Authorization: Bearer $TOKEN" http://staging-server:3002/script
   
   # Get user profile
   curl -H "Authorization: Bearer $TOKEN" http://staging-server:3002/user/me
   ```

3. **Database Verification**
   ```bash
   # Connect to staging database
   PGPASSWORD=password psql -h localhost -p 5433 -U trendsearth_staging -d trendsearth_staging
   
   # Check data
   SELECT COUNT(*) FROM script;
   SELECT role, COUNT(*) FROM "user" GROUP BY role;
   ```

## Monitoring and Logs

### Service Logs
```bash
# View staging service logs
docker service logs trends-earth-staging_manager
docker service logs trends-earth-staging_worker
docker service logs trends-earth-staging_postgres

# Follow logs in real-time
docker service logs -f trends-earth-staging_manager
```

### Database Logs
```bash
# PostgreSQL logs
docker logs $(docker ps -q -f name=trends-earth-staging-postgres)
```

### GitHub Actions Logs

Check the GitHub Actions workflow logs for:
- Database setup progress
- Migration statistics
- Test results
- Deployment status

## Troubleshooting

### Common Issues

1. **Database Connection Failed**
   ```bash
   # Check if PostgreSQL container is running
   docker ps | grep postgres
   
   # Verify database credentials
   PGPASSWORD=password psql -h localhost -p 5433 -U trendsearth_staging -d trendsearth_staging -c "SELECT 1"
   ```

2. **Migration Failed**
   ```bash
   # Check production database connectivity
   PGPASSWORD=prod-password psql -h prod-host -p 5432 -U prod-user -d trendsearth -c "SELECT COUNT(*) FROM script"
   
   # Verify recent scripts exist
   psql -c "SELECT COUNT(*) FROM script WHERE created_at >= NOW() - INTERVAL '1 year'"
   ```

3. **Test User Creation Failed**
   ```bash
   # Check if users were created
   psql -c "SELECT email, role FROM \"user\" WHERE email LIKE '%test%'"
   
   # Verify password hashing
   python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('test123'))"
   ```

4. **Authentication Failed**
   ```bash
   # Test password verification
   python3 -c "
   from werkzeug.security import check_password_hash
   # Get hash from database and test
   print(check_password_hash('hash-from-db', 'your-password'))
   "
   ```

### Reset Staging Environment

To completely reset the staging environment:

```bash
# Remove staging stack
docker stack rm trends-earth-staging

# Remove staging database
docker stop trends-earth-staging-postgres
docker rm trends-earth-staging-postgres

# Remove volumes (optional - destroys data)
docker volume rm trends-earth-staging_postgres_staging_data

# Redeploy
git push origin staging
```

## Security Considerations

### Staging-Specific Security

1. **Test User Passwords**: Use different passwords in production
2. **Database Isolation**: Staging database is separate from production
3. **Network Isolation**: Staging uses separate Docker networks
4. **Access Control**: Staging should not be publicly accessible

### Production Data Protection

1. **Limited Data**: Only scripts from the past year are copied
2. **No User Data**: Production user accounts are not copied
3. **Sanitization**: Consider sanitizing sensitive data before migration
4. **Access Logs**: Monitor who accesses staging environment

## Performance Considerations

### Resource Allocation

- **PostgreSQL**: 0.25 CPU, 200MB RAM
- **Application**: 0.25 CPU, 400MB RAM per service
- **Redis**: 0.25 CPU, 100MB RAM

### Optimization Tips

1. **Script Migration**: Consider limiting to fewer scripts if migration is slow
2. **Database Size**: Monitor staging database growth
3. **Cleanup**: Regularly clean up old staging data
4. **Indexing**: Ensure proper database indexes for staging queries

## Maintenance

### Regular Tasks

1. **Weekly**: Review staging deployment logs
2. **Monthly**: Clean up old staging data
3. **Quarterly**: Update test user passwords
4. **As Needed**: Refresh staging data from production

### Updates

To update the staging setup scripts:

1. Modify scripts in `scripts/deployment/`
2. Test changes locally
3. Push to `develop` or `staging` branch
4. Monitor deployment workflow

## 🔧 Automated Container-Based Setup

The staging workflow now uses fully automated container-based setup:

### Current Architecture
- **`migrate` service**: Runs automatically in Docker container to handle all database operations
- **`setup_staging_environment.py`**: Comprehensive setup script that runs inside the migrate container
- **`run-integration-tests.sh`**: Comprehensive API and authentication testing (still available for manual use)

### Benefits
- **Fully Automated**: No manual script execution required
- **Multi-Node Compatible**: Runs inside containers where database access is guaranteed
- **Comprehensive**: Handles migrations, user creation, and data import automatically
- **Reliable**: Consistent container environment with all dependencies
- **Error Handling**: Better error reporting and logging within containers
- **Security**: No external database connections from deployment nodes
- **Maintainability**: All setup logic contained in Python script within container

See [Scripts Documentation](../scripts/deployment/README.md) for detailed information.

The staging environment provides a complete, isolated testing environment with realistic data and proper user roles for comprehensive testing of the trends.earth API.
