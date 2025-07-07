# Trends.Earth API

[![Tests](https://github.com/conservationinternational/trends.earth-API/workflows/Run%20Tests/badge.svg)](https://github.com/conservationinternational/trends.earth-API/actions/workflows/run-tests.yml)
[![API Documentation](https://github.com/conservationinternational/trends.earth-API/workflows/Generate%20API%20Documentation/badge.svg)](https://github.com/conservationinternational/trends.earth-API/actions/workflows/generate-api-docs.yml)
[![Code Quality](https://img.shields.io/badge/code%20quality-ruff-blue.svg)](https://github.com/astral-sh/ruff)
[![Coverage](https://img.shields.io/badge/coverage-pytest--cov-green.svg)](https://pytest-cov.readthedocs.io/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

This project belongs to the Trends.Earth project and implements the API used by the Trends.Earth plugin and web interfaces. It manages Scripts, Users, Executions, and system monitoring.

## Related Projects

- [Trends.Earth CLI](https://github.com/conservationinternational/trends.earth-CLI) - Command Line Interface for creating and testing custom scripts
- [Trends.Earth Core Environment](https://github.com/conservationinternational/trends.earth-Environment) - Execution environment for running scripts
- [Trends.Earth UI](https://github.com/conservationinternational/trends.earth-UI) - Web interface for managing API entities

## Technology Stack

- **Python 3.11** - Main programming language (Alpine-based Docker image)
- **Poetry** - Dependency management and packaging
- **Flask** - Web framework for API endpoints
- **SQLAlchemy** - ORM for database operations (PostgreSQL)
- **Celery** - Background task management (with Redis)
- **Docker** - Containerization for development and production
- **Gunicorn** - WSGI server for production deployment
- **Flask-Migrate** - Database migration management

## Getting Started

### Requirements

- [Docker](https://www.docker.com/) and Docker Compose
- Git
- **Windows users**: WSL2 recommended for optimal Docker performance

### Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/conservationinternational/trends.earth-api
   cd trends.earth-api
   ```

2. **Build and start services:**
   ```bash
   docker compose -f docker-compose.staging.yml build
   docker compose -f docker-compose.staging.yml up
   ```

3. **Stop services:**
   ```bash
   docker compose -f docker-compose.staging.yml down
   ```

## Docker Services

The application is composed of several Docker services, each with a specific purpose:

### Core Services

- **`api`** - Main Flask application server
  - Handles HTTP requests and API endpoints
  - Runs on port 3000 (internal), exposed on port 3000 (development)
  - Command: `./entrypoint.sh start` (uses Gunicorn in production)
  - Command: `./entrypoint.sh develop` (direct Flask in development)

- **`worker`** - Celery worker for background tasks
  - Processes execution jobs and script builds
  - Command: `./entrypoint.sh worker`

- **`beat`** - Celery beat scheduler
  - Manages periodic tasks (system monitoring every 2 minutes)
  - Command: `./entrypoint.sh beat`

- **`migrate`** - Database migration service
  - Runs database migrations on startup
  - Command: `./entrypoint.sh migrate`
  - Uses the `run_db_migrations.py` script

### Supporting Services

- **`postgres`** - PostgreSQL database (version 9.6)
  - Stores all application data
  - Default port: 5432
  - Container name: `trendsearth-api-database` (development)

- **`redis`** - Redis message broker
  - Handles Celery task queues
  - Default port: 6379
  - Container name: `trendsearth-api-redis` (development)

- **`registry`** - Docker registry (development)
  - Local Docker image registry for development
  - Port: 5000
  - Container name: `trendsearth-api-registry`

- **`nginx`** (production) - Reverse proxy and load balancer
  - Serves static files and routes requests
  - Handles SSL termination

- **`test`** - Test execution service
  - Runs automated tests in isolated environment
  - Command: `./entrypoint.sh test`

## Docker Architecture

### Container Structure

The application uses a multi-container architecture with specialized services:

#### Core Application Containers
- **API Container**: Flask application server with Gunicorn (production) or direct Flask (development)
- **Worker Container**: Celery workers for background task processing
- **Beat Container**: Celery beat scheduler for periodic tasks
- **Migration Container**: Dedicated service for database schema migrations

#### Infrastructure Containers
- **PostgreSQL**: Primary database (version 9.6)
- **Redis**: Message broker and caching layer
- **Registry**: Local Docker registry for development images

### Environment-Specific Configurations

#### Development (`docker-compose.develop.yml`)
- Includes test database creation and migration services
- Volume mounts for live code reloading
- Exposes database and registry ports for local access
- Uses environment variables from `develop.env`

#### Staging (`docker-compose.staging.yml`)
- Production-like environment with external image registry
- Uses Docker Swarm deployment constraints
- Environment variables from `staging.env`
- No local volume mounts

#### Admin (`docker-compose.admin.yml`)
- Lightweight container for administrative tasks
- Direct access to production environment
- Used for manual migrations and database operations

### Entrypoint Commands

The `entrypoint.sh` script provides a unified interface for running different services:

```bash
develop    # Flask development server with auto-reload
start      # Gunicorn production server
worker     # Celery worker process
beat       # Celery beat scheduler
migrate    # Database migration execution
test       # Test suite execution
```

## Database Management

### Automated Migrations

The application now includes automated database migration handling:

1. **Automatic migration on startup:**
   ```bash
   # Migration service runs automatically in docker-compose
   docker compose -f docker-compose.develop.yml up migrate
   ```

2. **Manual migration execution:**
   ```bash
   # Run migration service directly
   docker compose -f docker-compose.develop.yml run --rm migrate
   
   # Or run migration command in any container
   docker exec -it <container_name> ./entrypoint.sh migrate
   ```

### Creating New Migrations

When you add new fields or modify existing models:

1. **Generate migration using admin container:**
   ```bash
   # Start admin container
   docker compose -f docker-compose.admin.yml up -d
   
   # Generate migration
   docker exec -it trendsearth-api-admin-1 flask db migrate -m "Description of changes"
   
   # Apply migration (optional, will be applied automatically on next startup)
   docker exec -it trendsearth-api-admin-1 flask db upgrade
   
   # Cleanup
   docker compose -f docker-compose.admin.yml down
   ```

2. **Alternative: Generate migration in development:**
   ```bash
   # Using development container
   docker compose -f docker-compose.develop.yml run --rm api flask db migrate -m "Description of changes"
   ```

### Migration Script

The new `run_db_migrations.py` script handles database migrations programmatically:
- Located at `/opt/gef-api/run_db_migrations.py` in containers
- Uses Flask-Migrate's `upgrade()` function
- Provides better error handling and logging
- Automatically executed by the `migrate` service

### Maintenance Container

For database operations and administrative tasks, use the admin container:

```bash
# Start maintenance container
docker compose -f docker-compose.admin.yml up -d

# Connect to container
docker exec -it trendsearth-api-admin-1 /bin/bash

# Run migrations manually (if needed)
flask db migrate -m "Add new field"
flask db upgrade

# Exit and cleanup
exit
docker compose -f docker-compose.admin.yml down
```

**Note:** The admin container automatically runs the `start` command, so it's primarily useful for accessing a shell environment with all dependencies loaded.

## API Endpoints

The API provides comprehensive filtering, sorting, and pagination capabilities for listing endpoints. All query parameters are optional, ensuring backward compatibility with existing implementations.

**Common Features:**
- **Filtering**: Support for date ranges, status filters, and field-specific filters
- **Sorting**: Sort by any field in ascending (default) or descending order (use `-` prefix)
- **Pagination**: Optional pagination (enabled only when `page` or `per_page` parameters are provided)
- **Field Control**: Use `include` to add extra fields and `exclude` to remove standard fields from responses

**Access Control & Security:**
- **User Information Restrictions**: For privacy and security, access to user names and email addresses is restricted to admin users only
- **Restricted Operations**: Non-admin users cannot filter, sort by, or include `user_name` or `user_email` fields
- **Error Handling**: Attempting to use restricted fields results in HTTP 403 Forbidden with a clear error message
- **Admin Privileges**: Users with `role: "ADMIN"` have unrestricted access to all user-related data

**Field Control Parameters:**
- `include` - Adds additional fields to the response (e.g., related objects, computed fields)
- `exclude` - Removes standard fields from the response to reduce payload size
- Both parameters can be used together: fields are first included, then excluded
- Use comma-separated values for multiple fields: `include=user,logs&exclude=description,params`

### Authentication
- `POST /auth` - User authentication

### Scripts
- `GET /api/v1/script` - List all scripts with filtering, sorting, and pagination
- `GET /api/v1/script/<script_id>` - Get specific script
- `POST /api/v1/script` - Create new script
- `PATCH /api/v1/script/<script_id>` - Update script
- `DELETE /api/v1/script/<script_id>` - Delete script (Admin only)
- `POST /api/v1/script/<script_id>/publish` - Publish script
- `POST /api/v1/script/<script_id>/unpublish` - Unpublish script
- `GET /api/v1/script/<script_id>/download` - Download script
- `GET /api/v1/script/<script_id>/log` - Get script logs

#### Script Filtering & Sorting

**Query Parameters:**
- `filter` - SQL-style filter expression(s), comma-separated. Supported operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `like`. Example: `status=SUCCESS,public=true,name like 'foo%'`
  - **Admin-only filters**: `user_name`, `user_email` (non-admin users will receive an error)
- `sort` - SQL-style sorting expression(s), comma-separated. Allows advanced multi-field sorting, e.g. `sort=status desc,name asc`. Supported fields: any column in the scripts table, plus `user_name`, `user_email`. Example: `sort=status desc,name asc` will sort by status descending, then by name ascending.
  - **Admin-only sorting**: `user_name`, `user_email` (non-admin users will receive an error)
- `include` - Comma-separated list of extra fields to include in each script result. Supported values:
  - `user`: include full user object as `user`
  - `user_name`: include only the user's name as `user_name` (**Admin only**)
  - `user_email`: include only the user's email as `user_email` (**Admin only**)
  - `logs`, `executions`, `environment`: see below
- `exclude` - Comma-separated list of fields to exclude from each script result. Can be used to remove any standard field from the response (e.g., `description,cpu_reservation,memory_limit`). Useful for reducing payload size when certain fields are not needed.
- `page` - Page number (only used if pagination is requested, defaults to 1)
- `per_page` - Items per page (only used if pagination is requested, defaults to 20, max: 100)

**Access Control:**
- **Regular users**: Can see their own scripts + public scripts
- **Admin users**: Can see all scripts and filter by `user_id`

**Pagination:**
By default, all scripts are returned without pagination. To enable pagination, include either `page` or `per_page` parameters in your request. When pagination is enabled, the response will include `page`, `per_page`, and `total` fields.

**Examples:**
```bash
# Get public scripts, sorted by creation date (newest first)
GET /api/v1/script?filter=public=true&sort=-created_at

# Get first page of scripts with pagination
GET /api/v1/script?page=1&per_page=10

# Get scripts created in the last week
GET /api/v1/script?filter=created_at>2025-06-19T00:00:00Z

# Get PENDING scripts for a specific user (Admin only)
GET /api/v1/script?filter=status=PENDING,user_id=550e8400-e29b-41d4-a716-446655440000

# Get scripts sorted by name (no pagination)
GET /api/v1/script?sort=name

# Get scripts filtered by user name (Admin only)
GET /api/v1/script?filter=user_name like '%john%'

# Get scripts sorted by user email (Admin only)
GET /api/v1/script?sort=user_email desc

# Get scripts and include user name in each result (Admin only)
GET /api/v1/script?include=user_name

# Get scripts and include user email in each result (Admin only)
GET /api/v1/script?include=user_email

# Get scripts and include full user object and logs
GET /api/v1/script?include=user,logs

# Get scripts but exclude large fields to reduce response size
GET /api/v1/script?exclude=description

# Get scripts with user names but exclude technical fields (Admin only)
GET /api/v1/script?include=user_name&exclude=cpu_reservation,cpu_limit,memory_reservation,memory_limit
```

**Error Examples for Non-Admin Users:**
```bash
# These requests will result in HTTP 403 Forbidden for non-admin users:

# Attempting to filter by user_name
GET /api/v1/script?filter=user_name like '%john%'
# Response: {"status": 403, "detail": "Access denied: Only admin users can filter by user_name"}

# Attempting to sort by user_email  
GET /api/v1/script?sort=user_email desc
# Response: {"status": 403, "detail": "Access denied: Only admin users can sort by user_email"}

# Attempting to include user_name in response
GET /api/v1/script?include=user_name
# Response: {"status": 403, "detail": "Access denied: Only admin users can include user_name in API responses"}
```

**Example Response with `include=user_name` (Admin only):**
```json
{
  "data": [
    {
      "id": "...",
      "name": "My Script",
      ...,
      "user_id": "...",
      "user_name": "Jane Doe"
    },
    ...
  ]
}
```

**Example Response with `include=user`:**
```json
{
  "data": [
    {
      "id": "...",
      "name": "My Script",
      ...,
      "user_id": "...",
      "user": {
        "id": "...",
        "name": "Jane Doe",
        ...
      }
    },
    ...
  ]
}
```

### Executions
- `POST /api/v1/script/<script_id>/run` - Run a script
- `GET /api/v1/execution` - List executions with filtering and sorting
- `GET /api/v1/execution/<execution_id>` - Get specific execution
- `PATCH /api/v1/execution/<execution_id>` - Update execution (Admin only)
- `GET /api/v1/execution/<execution_id>/log` - Get execution logs
- `POST /api/v1/execution/<execution_id>/log` - Add execution log (Admin only)
- `GET /api/v1/execution/<execution_id>/download-results` - Download results

#### Execution Filtering & Sorting

**Query Parameters:**
- `status` - Filter by execution status (e.g., `FINISHED`, `RUNNING`)
- `updated_at` - (Backwards compatibility) Filter executions updated after this date
- `sort` - SQL-style sorting expression(s), comma-separated. Allows advanced multi-field sorting, e.g. `sort=status desc,progress asc`. Supported fields: any column in the executions table, plus `duration`, `script_name`, `user_name`, `user_email`. Example: `sort=status desc,progress asc` will sort by status descending, then by progress ascending.
  - **Admin-only sorting**: `user_name`, `user_email` (non-admin users will receive an error)
- `include` - Comma-separated list of extra fields to include in each execution result. Supported values:
  - `duration`: include duration in seconds
  - `user`: include full user object as `user`
  - `user_name`: include only the user's name as `user_name` (**Admin only**)
  - `user_email`: include only the user's email as `user_email` (**Admin only**)
  - `script`: include full script object as `script`
  - `script_name`: include only the script's name as `script_name`
  - `logs`: include execution logs
- `exclude` - Comma-separated list of fields to exclude from each execution result. Can be used to remove any standard field from the response (e.g., `params,results,start_date`). Useful for reducing payload size when certain fields are not needed.
- `filter` - SQL-style filter expression(s), comma-separated. Supported operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `like`. Example: `progress>50,status=FINISHED`
  - **Admin-only filters**: `user_name`, `user_email` (non-admin users will receive an error)
- `page` - Page number (only used if pagination is requested, defaults to 1)
- `per_page` - Items per page (only used if pagination is requested, defaults to 20, max: 100)

**Pagination:**
By default, all executions are returned without pagination. To enable pagination, include either `page` or `per_page` parameters in your request. When pagination is enabled, the response will include `page`, `per_page`, and `total` fields.

**Examples:**
```bash
# Get all finished executions with duration, sorted by longest first (no pagination)
GET /api/v1/execution?status=FINISHED&include=duration&sort=-duration

# Get first page of executions (pagination enabled)
GET /api/v1/execution?page=1&per_page=10

# Get executions from last week, sorted by script name (no pagination)
GET /api/v1/execution?start_date_gte=2025-06-14&sort=script_name&include=script_name

# Get running executions with user info (Admin only)
GET /api/v1/execution?status=RUNNING&include=user,user_name,duration&sort=-start_date

# Get executions with progress > 50 and status FINISHED
GET /api/v1/execution?filter=progress>50,status=FINISHED

# Get executions where status is not FAILED
GET /api/v1/execution?filter=status!=FAILED

# Get executions where script_id matches a pattern
GET /api/v1/execution?filter=script_id like 'abc%'

# Get executions filtered by user name (Admin only)
GET /api/v1/execution?filter=user_name like '%john%'

# Get executions sorted by user email (Admin only)
GET /api/v1/execution?sort=user_email desc

# Get executions but exclude large fields to reduce response size
GET /api/v1/execution?exclude=params,results

# Get finished executions with user names but exclude parameters and results (Admin only)
GET /api/v1/execution?status=FINISHED&include=user_name&exclude=params,results

# Get executions with user email information (Admin only)
GET /api/v1/execution?include=user_email&exclude=params,results
```

**Error Examples for Non-Admin Users:**
```bash
# These requests will result in HTTP 403 Forbidden for non-admin users:

# Attempting to filter by user_name
GET /api/v1/execution?filter=user_name like '%john%'
# Response: {"status": 403, "detail": "Access denied: Only admin users can filter by user_name"}

# Attempting to sort by user_email
GET /api/v1/execution?sort=user_email desc  
# Response: {"status": 403, "detail": "Access denied: Only admin users can sort by user_email"}

# Attempting to include user_email in response
GET /api/v1/execution?include=user_email
# Response: {"status": 403, "detail": "Access denied: Only admin users can include user_email in API responses"}
```

**Example Response with `include=user_name,script_name` (Admin only for user_name):**
```json
{
  "data": [
    {
      "id": "...",
      "script_id": "...",
      "user_id": "...",
      "user_name": "Jane Doe",
      "script_name": "My Script",
      ...
    },
    ...
  ]
}
```

**Example Response with `include=user,script`:**
```json
{
  "data": [
    {
      "id": "...",
      "script_id": "...",
      "user_id": "...",
      "user": {
        "id": "...",
        "name": "Jane Doe",
        ...
      },
      "script": {
        "id": "...",
        "name": "My Script",
        ...
      }
    },
    ...
  ]
}
```

### Users
- `GET /api/v1/user` - List all users with filtering, sorting, and pagination (Admin only)
- `GET /api/v1/user/<user_id>` - Get specific user (Admin only)
- `GET /api/v1/user/me` - Get current user profile
- `POST /api/v1/user` - Create new user
- `PATCH /api/v1/user/<user_id>` - Update user (Admin only)
- `PATCH /api/v1/user/me` - Update own profile
- `DELETE /api/v1/user/<user_id>` - Delete user (Admin only)
- `DELETE /api/v1/user/me` - Delete own account
- `POST /api/v1/user/<user_id>/recover-password` - Password recovery

#### User Filtering & Sorting (Admin Only)

**Query Parameters:**
- `filter` - SQL-style filter expression(s), comma-separated. Supported operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `like`. Example: `role=ADMIN,country like 'US%'`
- `sort` - SQL-style sorting expression(s), comma-separated. Allows advanced multi-field sorting, e.g. `sort=name asc,email desc`. Supported fields: any column in the users table. Example: `sort=name asc,email desc` will sort by name ascending, then by email descending.
- `include` - Comma-separated list of extra fields to include in each user result. Supported values:
  - `scripts`: include user's scripts
- `exclude` - Comma-separated list of fields to exclude from each user result. Can be used to remove any standard field from the response (e.g., `institution,country`). Useful for reducing payload size when certain fields are not needed.
- `page` - Page number (only used if pagination is requested, defaults to 1)
- `per_page` - Items per page (only used if pagination is requested, defaults to 20, max: 100)

**Access Control:**
- **Admin only**: Only administrators and the special `gef@gef.com` user can access this endpoint

**Pagination:**
By default, all users are returned without pagination. To enable pagination, include either `page` or `per_page` parameters in your request. When pagination is enabled, the response will include `page`, `per_page`, and `total` fields.

**Examples:**
```bash
# Get users from USA, sorted by name
GET /api/v1/user?filter=country=USA&sort=name

# Get first page of admin users
GET /api/v1/user?filter=role=ADMIN&page=1&per_page=10

# Get users created in the last month
GET /api/v1/user?filter=created_at>2025-05-26T00:00:00Z

# Get users from universities (partial match)
GET /api/v1/user?filter=institution like 'University%'

# Get users sorted by creation date (newest first, no pagination)
GET /api/v1/user?sort=-created_at

# Get users but exclude sensitive fields to reduce response size
GET /api/v1/user?exclude=institution,country

# Get admin users with scripts but exclude personal information
GET /api/v1/user?filter=role=ADMIN&include=scripts&exclude=institution,country
```
### System Status (Admin Only)
- `GET /api/v1/status` - Get system status logs

**Query Parameters:**
- `start_date` - Filter logs from date
- `end_date` - Filter logs until date
- `sort` - Sort by timestamp (default: `-timestamp`)
- `page` - Page number
- `per_page` - Items per page (max: 1000)

**Status Metrics:**
- Execution counts by status (active, ready, running, finished)
- Total users and scripts count
- System memory availability percentage
- CPU usage percentage
- Timestamp of measurement

### Other Endpoints
- `POST /email` - Send email

## Security and Access Control

### Admin-Only User Data Access

For security and privacy protection, access to user-related information is restricted to administrators:

**Restricted Operations:**
- **Filtering by user fields**: `filter=user_name like '%john%'` or `filter=user_email=user@example.com`
- **Sorting by user fields**: `sort=user_name desc` or `sort=user_email asc`
- **Including user fields**: `include=user_name` or `include=user_email`

**Affected Endpoints:**
- `GET /api/v1/script` - Script listing with user data restrictions
- `GET /api/v1/execution` - Execution listing with user data restrictions

**Error Response:**
Non-admin users attempting to access restricted user data will receive:
```json
{
  "status": 403,
  "detail": "Access denied: Only admin users can filter/sort/include user information"
}
```

**Admin Access:**
Users with `role: "ADMIN"` can access all functionality without restrictions and can:
- Filter scripts and executions by any user field
- Sort results by user name or email
- Include user names and emails in API responses
- Access user management endpoints

**Backward Compatibility:**
- All existing functionality remains unchanged for non-restricted fields
- Public fields and standard filtering/sorting continue to work for all users
- The `user` object can still be included by any user (contains public user information)

## Data Models

### Script
```python
{
    "id": "UUID",
    "name": "string",
    "slug": "string (unique)",
    "created_at": "datetime",
    "user_id": "UUID",
    "status": "string",
    "public": "boolean",
    "cpu_reservation": "integer",
    "cpu_limit": "integer", 
    "memory_reservation": "integer",
    "memory_limit": "integer"
}
```

### Execution
```python
{
    "id": "UUID",
    "start_date": "datetime",
    "end_date": "datetime",
    "status": "string",
    "progress": "integer",
    "params": "object",
    "results": "object",
    "script_id": "UUID",
    "user_id": "UUID",
    "duration": "float (seconds, when included)"
}
```

### User
```python
{
    "id": "UUID",
    "created_at": "datetime",
    "email": "string (unique)",
    "role": "string",
    "name": "string",
    "country": "string",
    "institution": "string"
}
```

### Status Log
```python
{
    "id": "integer",
    "timestamp": "datetime",
    "executions_active": "integer",
    "executions_ready": "integer", 
    "executions_running": "integer",
    "executions_finished": "integer",
    "users_count": "integer",
    "scripts_count": "integer",
    "memory_available_percent": "float",
    "cpu_usage_percent": "float"
}
```

## Development

### Local Development

#### Development Environment Setup

```bash
# Build and start all development services (includes automatic migration)
docker compose -f docker-compose.develop.yml up --build

# Start specific services
docker compose -f docker-compose.develop.yml up api worker

# Start with migration first (recommended for fresh setups)
docker compose -f docker-compose.develop.yml up migrate database redis
docker compose -f docker-compose.develop.yml up api

# View logs
docker compose -f docker-compose.develop.yml logs -f api

# Stop all services
docker compose -f docker-compose.develop.yml down
```

#### Container Commands

The `entrypoint.sh` script supports multiple commands:

```bash
# Development server (direct Flask, auto-reload)
docker compose -f docker-compose.develop.yml run --rm api develop

# Production server (Gunicorn)
docker compose -f docker-compose.develop.yml run --rm api start

# Celery worker
docker compose -f docker-compose.develop.yml run --rm api worker

# Celery beat scheduler
docker compose -f docker-compose.develop.yml run --rm api beat

# Database migrations
docker compose -f docker-compose.develop.yml run --rm api migrate

# Run tests
docker compose -f docker-compose.develop.yml run --rm api test
```

#### Testing

```bash
# Recommended: Use the test script (handles service dependencies automatically)
./run_tests.sh

# Run specific test files or patterns
./run_tests.sh tests/test_smoke.py
./run_tests.sh -k "test_environment"

# Alternative: Manual test execution
docker compose -f docker-compose.develop.yml up -d database redis
docker compose -f docker-compose.develop.yml run --rm test

# Run tests with specific parameters
docker compose -f docker-compose.develop.yml run --rm test pytest -v tests/
```

### Testing Infrastructure

The testing setup includes dedicated services and automated scripts:

#### Test Service Configuration
- **Isolated Environment**: Tests run in a separate container with `test.env` configuration
- **Database Setup**: Automatic test database creation (`gef_test`)
- **Service Dependencies**: Automated startup of required PostgreSQL and Redis services

#### Test Execution Scripts
- **`run_tests.sh`**: Automated test runner that handles service orchestration
- **Service Management**: Automatically starts dependencies, runs tests, and cleans up
- **Flexible Testing**: Supports specific test files, patterns, and pytest arguments

#### Test Environment Features
- **Environment Isolation**: `TESTING=true` and `ENVIRONMENT=test` flags
- **Dependency Management**: Tests run with all production dependencies via Poetry
- **Volume Mounting**: Live code access for debugging and development

#### Development Dependencies

The project uses **Poetry** for dependency management:
- `pyproject.toml` - Main dependency configuration
- `poetry.lock` - Locked dependency versions
- Dependencies are installed during Docker image build

### Code Structure

```
gefapi/
├── models/          # SQLAlchemy models
├── routes/          # Flask routes and endpoints
├── services/        # Business logic layer
├── tasks/           # Celery background tasks
├── config/          # Configuration files
├── validators/      # Request validation
└── errors.py        # Custom exceptions
```

## Deployment

### Environment Variables

Key environment variables to configure (see `.env` files):

#### Database Configuration
- `DATABASE_URL` - PostgreSQL connection string
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` - Database credentials

#### Redis Configuration  
- `REDIS_URL` - Redis connection string for Celery

#### Application Configuration
- `JWT_SECRET_KEY` - Secret key for JWT tokens
- `ENVIRONMENT` - Application environment (`dev`, `test`, `staging`, `prod`)
- `DEBUG` - Debug mode flag
- `TESTING` - Testing mode flag
- `PORT` - Application port (default: 3000)

#### External Services
- `SMTP_*` - Email configuration for notifications
- `AWS_*` - S3 bucket configuration for file storage

#### Container Configuration
- Docker socket mounting: `/var/run/docker.sock:/tmp/docker.sock`
- User/group configuration for container security

#### Environment Files
- `develop.env` - Development environment settings
- `test.env` - Testing environment settings  
- `staging.env` - Staging environment settings
- `prod2.env` - Production environment settings

### Production Deployment

#### Building and Deploying

1. **Build and push image:**
   ```bash
   # Build image with Poetry dependencies
   docker build -t registry.example.com/trendsearth-api .
   docker push registry.example.com/trendsearth-api
   ```

2. **Deploy with Docker Swarm:**
   ```bash
   # Deploy full stack including migration service
   docker stack deploy -c docker-compose.prod.yml api
   ```

#### Docker Image Details

The production Docker image:
- Based on `python:3.11-alpine` for minimal size and security
- Uses Poetry for dependency management (virtualenvs disabled in container)
- Includes all application code and migrations
- Runs as `gef-api` user for security (currently disabled, runs as root)
- Supports multiple entry points via `entrypoint.sh`

#### Service Orchestration

Production deployment includes:
- **Migrate service**: Runs database migrations before other services start
- **Manager service**: Main API server (Gunicorn)
- **Worker service**: Celery background task processor
- **Beat service**: Celery periodic task scheduler
- **Redis service**: Message broker and cache

## Monitoring

The system automatically collects metrics every 2 minutes:
- Execution status counts
- User and script totals
- System resource usage (CPU, memory)

Access monitoring data via the `/api/v1/status` endpoint (Admin only).

## API Documentation

### Recent Improvements

- **Field Exclusion Support**: All serialization methods (`Script`, `User`, `Execution`) now support the `exclude` parameter to remove unwanted fields from API responses, improving performance and reducing payload sizes.
- **Consistent Parameter Handling**: The `include` and `exclude` parameters are now consistently supported across all GET endpoints for scripts, users, and executions.

### Automatic Documentation Generation

The API documentation is automatically generated using OpenAPI/Swagger whenever code is pushed to the main branch:

- **Swagger JSON**: Available at `/swagger.json` in the repository
- **Interactive UI**: Generated HTML documentation in `/docs/swagger-ui/`
- **Static Docs**: Comprehensive API reference in `/docs/api/`

### Generating Documentation Locally

To generate API documentation locally:

```bash
# Install additional dependencies
pip install flask-restx apispec[flask] marshmallow

# Generate OpenAPI specification
python generate_swagger.py

# This creates swagger.json with complete API specification
```

### GitHub Workflow

The `.github/workflows/generate-api-docs.yml` workflow automatically:

1. **Triggers on**: Push to `main` or `develop` branches, and pull requests
2. **Sets up environment**: Python, PostgreSQL, Redis
3. **Generates documentation**: Creates OpenAPI spec and HTML docs
4. **Commits changes**: Auto-commits updated documentation (main branch only)
5. **Provides artifacts**: Uploads docs as downloadable artifacts

### Accessing Documentation

- **Development**: Run `python generate_swagger.py` and open `swagger.json` in Swagger Editor
- **Production**: Documentation is automatically deployed and available in the `docs/` directory
- **API Testing**: Use the interactive Swagger UI to test endpoints directly

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

**Note**: API documentation will be automatically updated when your changes are merged to the main branch.

## Troubleshooting

### Docker Authentication Issues

#### Credential Storage Errors (WSL/Linux)

If you encounter credential storage errors like:
```
WARNING: error getting credentials - err: exit status 1, out: ``
failed to store tokens: error storing credentials - err: exit status 1, out: ``
```

**Solution 1: Use Docker Desktop Integration (Recommended for WSL)**
```bash
# If using WSL with Docker Desktop, ensure Docker Desktop is running
# and WSL integration is enabled in Docker Desktop settings
```

**Solution 2: Configure Docker to Use Plain Text Storage**
```bash
# Create or edit Docker config
mkdir -p ~/.docker
echo '{"credsStore":""}' > ~/.docker/config.json

# Then retry login
docker login
```

**Solution 3: Remove Credential Helper**
```bash
# Edit Docker config to remove credential store
nano ~/.docker/config.json

# Remove or comment out the "credsStore" line:
{
  // "credsStore": "desktop",
  "credHelpers": {}
}
```

**Solution 4: Reset Docker Configuration**
```bash
# Backup existing config
cp ~/.docker/config.json ~/.docker/config.json.backup

# Remove problematic config
rm ~/.docker/config.json

# Retry authentication
docker login
```

#### Container Permission Issues

**Issue**: Permission denied when running containers or accessing mounted volumes.

**Solutions**:
```bash
# Option 1: Add user to docker group (Linux)
sudo usermod -aG docker $USER
newgrp docker

# Option 2: Run with sudo (temporary fix)
sudo docker compose up

# Option 3: Fix file permissions for mounted volumes
sudo chown -R $USER:$USER /path/to/project
```

### Development Environment Issues

#### Port Already in Use
```bash
# Check what's using the port
sudo netstat -tulpn | grep :3000
# or
sudo lsof -i :3000

# Kill the process using the port
sudo kill -9 <PID>

# Or use different ports in docker-compose
ports:
  - "3001:3000"  # Map to different host port
```

#### Database Connection Issues
```bash
# Check if PostgreSQL container is running
docker compose -f docker-compose.develop.yml ps

# View database logs
docker compose -f docker-compose.develop.yml logs database

# Reset database
docker compose -f docker-compose.develop.yml down -v
docker compose -f docker-compose.develop.yml up database
```

#### Migration Failures
```bash
# Check migration logs
docker compose -f docker-compose.develop.yml logs migrate

# Manual migration execution
docker compose -f docker-compose.develop.yml run --rm api flask db upgrade

# Reset migrations (WARNING: destroys data)
docker compose -f docker-compose.develop.yml down -v
docker compose -f docker-compose.develop.yml up migrate
```

### Testing Issues

#### Test Database Connection
```bash
# Ensure test services are running
docker compose -f docker-compose.develop.yml up -d database redis

# Check if test database exists
docker compose -f docker-compose.develop.yml exec database psql -U root -d postgres -c "\l"

# Create test database manually if needed
docker compose -f docker-compose.develop.yml exec database psql -U root -d postgres -c "CREATE DATABASE gef_test;"
```

#### Test Environment Variables
```bash
# Verify test.env file exists and is properly configured
cat test.env

# Check environment variables in test container
docker compose -f docker-compose.develop.yml run --rm test env | grep -E "(DATABASE|REDIS|TESTING)"
```

### Performance Issues

#### Container Resource Limits
```bash
# Check container resource usage
docker stats

# Monitor specific container
docker stats trendsearth-api-api-1

# Increase memory limits in docker-compose.yml if needed
deploy:
  resources:
    limits:
      memory: 2G
    reservations:
      memory: 1G
```

#### Volume Mount Performance (Windows/WSL)
```bash
# For better performance on Windows, clone to WSL filesystem
cd ~/
git clone https://github.com/conservationinternational/trends.earth-api
cd trends.earth-api

# Avoid mounting from /mnt/c/ if possible
```

### Getting Help

If you continue experiencing issues:

1. **Check Docker Desktop Status**: Ensure Docker Desktop is running and updated
2. **Review Logs**: Use `docker compose logs <service>` to see detailed error messages
3. **Environment Variables**: Verify all required `.env` files are present and configured
4. **Clean Rebuild**: Try `docker compose down -v && docker compose build --no-cache && docker compose up`
5. **System Resources**: Ensure sufficient disk space and memory for containers

For persistent issues, please create an issue in the repository with:
- Error messages and logs
- Operating system and Docker version
- Docker Compose file being used
- Steps to reproduce the problem
