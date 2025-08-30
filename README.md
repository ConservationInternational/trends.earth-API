# Trends.Earth API

[![Tests](https://github.com/conservationinternational/trends.earth-API/workflows/Run%20Tests/badge.svg)](https://github.com/conservationinternational/trends.earth-API/actions/workflows/run-tests.yml)
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
- **Celery** - Background task management with periodic tasks (execution cleanup, finished execution cleanup, old failed execution cleanup)
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

2. **Set up environment files:**
   ```bash
   # Copy and customize environment configuration (required - these files are gitignored)
   cp .env.example develop.env
   cp .env.example test.env
   
   # Optional: Configure Docker security for Linux/WSL users
   # ./scripts/setup-docker-security.sh
   ```

3. **Build and start development services:**
   ```bash
   docker compose -f docker-compose.develop.yml build
   docker compose -f docker-compose.develop.yml up
   ```

4. **Stop development services:**
   ```bash
   docker compose -f docker-compose.develop.yml down
   ```

**For testing:**
```bash
# Run tests using the recommended script
./run_tests.sh

# Or run specific tests
./run_tests.sh tests/test_smoke.py
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
  - Manages periodic tasks (execution cleanup every hour, finished execution cleanup daily, old failed execution cleanup daily)
  - Command: `./entrypoint.sh beat`

- **`migrate`** - Database migration service
  - Runs database migrations on startup
  - Command: `./entrypoint.sh migrate`
  - Uses the `run_db_migrations.py` script

### Supporting Services

- **`postgres`** - PostgreSQL database (version 16)
  - Stores all application data
  - Default port: 5432
  - Container name: `trendsearth-api-postgres` (development)

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
- **Beat Container**: Celery beat scheduler for periodic tasks (cleanup, maintenance)
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
- Minimal container for administrative tasks and direct access to production environment
- Contains only the main API service without background workers or dependencies
- Used for manual migrations, database operations, and administrative access
- No Redis or Docker build capabilities (lightweight deployment)

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

## Configuration

### Docker Registry and Network Configuration

The application supports configurable Docker registry and network settings via environment variables for flexible deployment across different environments.

**Registry Configuration:**
- `REGISTRY_HOST` - Docker registry host and port (default: `registry.example.com:5000`)
  - Set this to your private registry: `REGISTRY_HOST=my-registry.example.com:5000`
  - Used for pulling application images in production and staging deployments

**Network Configuration:**
- `DOCKER_SUBNET` - Subnet for Docker overlay networks
  - Production default: `10.10.0.0/16`
  - Staging default: `10.1.0.0/16`
  - Customize for your network requirements: `DOCKER_SUBNET=172.20.0.0/16`

**Environment Files:**
Create environment-specific files (`.env`, `prod.env`, `staging.env`) with these variables:
```bash
# Example configuration
REGISTRY_HOST=my-registry.example.com:5000
DOCKER_SUBNET=10.10.0.0/16
```

### Rate Limiting Configuration

The API includes configurable rate limiting to protect against abuse and ensure fair usage. Rate limiting settings are configured via environment variables and can be customized per deployment environment.

**Configuration Variables:**
- `RATE_LIMITING_ENABLED` - Enable/disable rate limiting (default: `true`)
- `RATE_LIMIT_STORAGE_URI` - Storage backend URI (Redis URL or `memory://` for in-memory storage)

**Rate Limit Types:**
- **Authentication**: Limits login attempts to prevent brute force attacks
- **User Creation**: Limits account registration to prevent spam accounts
- **Script Execution**: Limits script runs to manage computational resources
- **Password Recovery**: Strict limits on password reset requests
- **API Endpoints**: General limits for API access

**Admin Exemptions:**
- Users with `ADMIN` or `SUPERADMIN` roles are automatically exempt from all rate limits
- Rate limits are applied per user (for authenticated requests) or per IP address (for unauthenticated requests)

**Testing Configuration:**
- Test environments use lower limits for faster testing
- In-memory storage (`memory://`) is used instead of Redis for test isolation

## API Endpoints

The API provides comprehensive filtering, sorting, and pagination capabilities for listing endpoints. All query parameters are optional, ensuring backward compatibility with existing implementations.

### Interactive API Documentation

The API includes interactive documentation powered by Swagger UI with locally hosted assets:

- **Development**: `http://localhost:3000/api/docs/`
- **Staging**: `https://staging-api.trends.earth/api/docs/`
- **Production**: `https://api.trends.earth/api/docs/`

The interactive documentation allows you to:
- Browse all available endpoints
- View request/response schemas
- Test API calls directly from the browser
- Download the OpenAPI specification

**Common Features:**
- **Filtering**: Support for date ranges, status filters, and field-specific filters
- **Sorting**: Sort by any field in ascending (default) or descending order (use `-` prefix)
- **Pagination**: Optional pagination (enabled only when `page` or `per_page` parameters are provided)
- **Field Control**: Use `include` to add extra fields and `exclude` to remove standard fields from responses

**Access Control & Security:**
- **User Information Restrictions**: For privacy and security, access to user names and email addresses is restricted to admin users only
- **Restricted Operations**: Non-admin users cannot filter, sort by, or include `user_name` or `user_email` fields
- **Error Handling**: Attempting to use restricted fields results in HTTP 403 Forbidden with a clear error message
- **Admin Privileges**: Users with `role: "ADMIN"` or `role: "SUPERADMIN"` have unrestricted access to all user-related data
- **SuperAdmin Privileges**: Users with `role: "SUPERADMIN"` have exclusive access to user management operations (role changes, user deletion, password changes, profile updates)
- **Rate Limiting**: API endpoints are protected with configurable rate limits to prevent abuse
  - Different limits for different endpoint types (authentication, user creation, script execution, password recovery)
  - Admin and SuperAdmin users are automatically exempt from all rate limits
  - Rate limits are applied per user (authenticated) or per IP address (unauthenticated)
  - Rate limit storage can be configured to use Redis or in-memory storage
  - SuperAdmin users can query current rate limiting status via the `/api/v1/rate-limit/status` endpoint
  - SuperAdmin users can reset all rate limits via the `/api/v1/rate-limit/reset` endpoint

**Field Control Parameters:**
- `include` - Adds additional fields to the response (e.g., related objects, computed fields)
- `exclude` - Removes standard fields from the response to reduce payload size
- Both parameters can be used together: fields are first included, then excluded
- Use comma-separated values for multiple fields: `include=user,logs&exclude=description,params`

### Health Check
- `GET /api-health` - API health check and database connectivity status
  - No authentication required
  - Returns server status, timestamp, database connectivity, and API version
  - Used by load balancers and monitoring systems

### Status Tracking and Monitoring

The API provides advanced status tracking capabilities for monitoring execution states and system health.

#### Status Endpoint
- `GET /api/v1/status` - Retrieve execution status logs (Admin+ required)
  - Returns paginated status log entries with execution counts by state
  - Supports filtering by date range and sorting
  - Provides real-time insights into system execution patterns

**Status Log Fields:**
- `executions_active`: Total active executions (RUNNING + PENDING)
- `executions_ready`: Executions ready to run
- `executions_running`: Currently executing scripts
- `executions_finished`: Number of completed executions
- `executions_failed`: Number of failed executions
- `executions_cancelled`: Number of cancelled executions

#### Event-Driven Status Tracking

The system uses an **event-driven approach** for status tracking:
- Status logs are created automatically when execution status changes
- No periodic background tasks needed for status collection
- Real-time tracking provides immediate insights into execution state changes
- Each status change triggers a snapshot of current execution counts across all states

**Key Benefits:**
- **Real-time updates**: Status changes are logged immediately
- **Reduced resource usage**: No periodic polling of database
- **Event accuracy**: Each status change is captured precisely
- **Historical tracking**: Complete audit trail of execution state transitions

**Implementation:**
- Helper function `update_execution_status_with_logging()` handles all status updates
- Integrated into execution lifecycle (start, progress, completion, cancellation)
- Automatic counting of executions by state for each status log entry

### Authentication

The API uses JWT (JSON Web Tokens) with refresh token support for secure authentication.

#### Authentication Endpoints

**Login (Get Tokens):**
```bash
POST /auth
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "user_password"
}

# Response:
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "refresh_token": "dGhpc0lzQVJlZnJlc2hUb2tlbg...",
  "user_id": "user-uuid-here",
  "expires_in": 3600
}
```

**Refresh Access Token:**
```bash
POST /auth/refresh
Content-Type: application/json

{
  "refresh_token": "dGhpc0lzQVJlZnJlc2hUb2tlbg..."
}

# Response:
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "user_id": "user-uuid-here",
  "expires_in": 3600
}
```

**Logout (Revoke Refresh Token):**
```bash
POST /auth/logout
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "refresh_token": "dGhpc0lzQVJlZnJlc2hUb2tlbg..."
}
```

**Logout from All Devices:**
```bash
POST /auth/logout-all
Authorization: Bearer <access_token>
```

#### Token Details

- **Access Token**: Short-lived (1 hour), used for API requests
- **Refresh Token**: Long-lived (30 days), used to get new access tokens
- **Security**: Access tokens automatically expire, refresh tokens can be revoked
- **Usage**: Include access token in `Authorization: Bearer <token>` header

#### Session Management

**Get Active Sessions:**
```bash
GET /api/v1/user/me/sessions
Authorization: Bearer <access_token>

# Response includes session details:
{
  "data": [
    {
      "id": "session-uuid",
      "expires_at": "2025-08-11T12:00:00Z",
      "created_at": "2025-07-12T12:00:00Z",
      "last_used_at": "2025-07-12T14:30:00Z",
      "device_info": "IP: 192.168.1.1 | UA: Mozilla/5.0...",
      "is_revoked": false
    }
  ]
}
```

**Revoke Specific Session:**
```bash
DELETE /api/v1/user/me/sessions/<session_id>
Authorization: Bearer <access_token>
```

**Revoke All Sessions:**
```bash
DELETE /api/v1/user/me/sessions
Authorization: Bearer <access_token>
```

### Scripts
- `GET /api/v1/script` - List all scripts with filtering, sorting, and pagination
- `GET /api/v1/script/<script_id>` - Get specific script
- `POST /api/v1/script` - Create new script
- `PATCH /api/v1/script/<script_id>` - Update script
  - **Note**: If a file is included in the update, the system will attempt to build a new Docker image. If the build fails, the API will return a `500 Internal Server Error` and set the script's status to `FAILED`.
- `DELETE /api/v1/script/<script_id>` - Delete script (Admin only)
- `POST /api/v1/script/<script_id>/publish` - Publish script
- `POST /api/v1/script/<script_id>/unpublish` - Unpublish script
- `GET /api/v1/script/<script_id>/download` - Download script
- `GET /api/v1/script/<script_id>/log` - Get script logs

#### Script Access Control
- `GET /api/v1/script/<script_id>/access` - Get script access control information
- `PUT /api/v1/script/<script_id>/access/roles` - Set allowed roles for script access
- `PUT /api/v1/script/<script_id>/access/users` - Set allowed users for script access
- `POST /api/v1/script/<script_id>/access/users/<user_id>` - Add user to script access
- `DELETE /api/v1/script/<script_id>/access/users/<user_id>` - Remove user from script access
- `POST /api/v1/script/<script_id>/access/roles/<role>` - Add role to script access
- `DELETE /api/v1/script/<script_id>/access/roles/<role>` - Remove role from script access
- `DELETE /api/v1/script/<script_id>/access` - Clear all access restrictions

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
- **Regular users**: Can see their own scripts + public scripts + scripts they have explicit access to
- **Admin users**: Can see all scripts and filter by `user_id`
- **Script access restrictions**: Scripts can be restricted to specific roles or users (see Script Access Control section)

**Script Access Control:**
Scripts support fine-grained access control beyond the basic public/private model:
- `restricted`: Boolean indicating if script has access restrictions
- Role-based access: Restrict to specific user roles (USER, ADMIN, SUPERADMIN)
- User-based access: Restrict to specific individual users
- Hybrid access: Combine role and user restrictions

Example script response with access control:
```json
{
  "id": "script-id",
  "name": "My Restricted Script",
  "public": false,
  "restricted": true,
  "allowed_roles": ["ADMIN", "SUPERADMIN"],
  "allowed_users": ["user-id-1", "user-id-2"]
}
```

See [Script Access Control Documentation](docs/script-access-control.md) for detailed usage.

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
- `POST /api/v1/execution/<execution_id>/cancel` - Cancel execution and associated GEE tasks
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

**Example Response with `include=user_name,script_name` (Admin only):**
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
- `PATCH /api/v1/user/me/change-password` - Change own password
- `PATCH /api/v1/user/<user_id>/change-password` - Change user password (Admin only)
- `DELETE /api/v1/user/<user_id>` - Delete user (Admin only)
- `DELETE /api/v1/user/me` - Delete own account
- `POST /api/v1/user/<user_id>/recover-password` - Password recovery

### Execution Cancellation
- `POST /api/v1/execution/<execution_id>/cancel` - Cancel a running execution
  - **Access**: Requires authenticated user who owns the execution or Admin privileges
  - **Purpose**: Cancels a running execution by stopping the Docker service/container and any associated Google Earth Engine tasks
  - **Parameters**:
    - `execution_id`: The ID of the execution to cancel
  - **Response**: Returns the updated execution object with cancellation status
  - **Process**:
    1. Validates user permissions (execution owner or Admin)
    2. Stops and removes the Docker service/container
    3. Extracts Google Earth Engine task IDs from execution logs
    4. Attempts to cancel any found GEE tasks via Google Earth Engine REST API
    5. Updates execution status in database
  - **Example Request**:
    ```bash
    curl -X POST \
      https://api.trends.earth/api/v1/execution/12345/cancel \
      -H "Authorization: Bearer <jwt_token>" \
      -H "Content-Type: application/json"
    ```
  - **Example Response**:
    ```json
    {
      "data": {
        "type": "execution",
        "id": "12345",
        "attributes": {
          "status": "CANCELLED",
          "start_date": "2023-01-15T10:00:00Z",
          "end_date": "2023-01-15T10:05:30Z",
          "logs": "...",
          "script": "land_cover",
          "params": {...}
        }
      }
    }
    ```
  - **Error Responses**:
    - `403 Forbidden` - User does not own execution and is not Admin
    - `404 Not Found` - Execution not found
    - `400 Bad Request` - Execution is not in a cancellable state
    - `500 Internal Server Error` - Failed to cancel execution

### Rate Limiting Management
- `GET /api/v1/rate-limit/status` - Query current rate limiting status (SuperAdmin only)
  - **Access**: Restricted to users with `role: "SUPERADMIN"`
  - **Purpose**: Provides visibility into current rate limiting state across the system
  - **Response Data**:
    - `enabled`: Whether rate limiting is currently active
    - `storage_type`: Type of storage backend being used (e.g., Redis, Memory)
    - `total_active_limits`: Count of currently active rate limits
    - `active_limits`: Array of active rate limit entries, each containing:
      - `key`: The rate limit identifier (user:id, ip:address, etc.)
      - `type`: Type of limit ("user", "ip", "auth")
      - `identifier`: The specific user ID or IP address being limited
      - `current_count`: Current number of requests counted against the limit
      - `time_window_seconds`: Time window for the rate limit
      - `user_info`: User details (for user-type limits) including id, email, name, role
  - **Example Request**:
    ```bash
    curl -X GET \
      https://api.trends.earth/api/v1/rate-limit/status \
      -H "Authorization: Bearer <superadmin_jwt_token>"
    ```
  - **Use Cases**:
    - Monitor which users or IP addresses are currently rate limited
    - Investigate rate limiting issues reported by users
    - System monitoring and observability
    - Debug rate limiting configuration problems
  - **Error Responses**:
    - `403 Forbidden` - User does not have SuperAdmin privileges
    - `401 Unauthorized` - Valid JWT token required
    - `500 Internal Server Error` - Failed to query rate limiting status

- `POST /api/v1/rate-limit/reset` - Reset all rate limits (SuperAdmin only)
  - **Access**: Restricted to users with `role: "SUPERADMIN"`
  - **Purpose**: Clears all current rate limit counters across the system
  - **Use Cases**: 
    - Emergency situations where legitimate users are being rate limited
    - Testing and development environments
    - After configuration changes to rate limiting policies
  - **Response**: Returns success message when rate limits are successfully cleared
  - **Error Responses**: 
    - `403 Forbidden` - User does not have SuperAdmin privileges
    - `401 Unauthorized` - Valid JWT token required
    - `500 Internal Server Error` - Failed to reset rate limits

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

# Get superadmin users
GET /api/v1/user?filter=role=SUPERADMIN&page=1&per_page=10

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

#### Password Management

**Change Own Password:**
```bash
# Users can change their own password
PATCH /api/v1/user/me/change-password
Content-Type: application/json

{
  "old_password": "current_password",
  "new_password": "new_password"
}
```

**Admin Change User Password:**
```bash
# Admin users can change any user's password (no old password required)
PATCH /api/v1/user/<user_id>/change-password
Content-Type: application/json

{
  "new_password": "new_password"
}
```

**Request/Response Examples:**
```bash
# Change own password
curl -X PATCH "https://api.example.com/api/v1/user/me/change-password" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "old_password": "myOldPassword123",
    "new_password": "myNewPassword456"
  }'

# Admin change user password
curl -X PATCH "https://api.example.com/api/v1/user/550e8400-e29b-41d4-a716-446655440000/change-password" \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "new_password": "temporaryPassword123"
  }'
```

**Error Responses:**
```json
// Invalid old password (for user self-change)
{
  "status": 401,
  "detail": "Invalid current password"
}

// Missing required fields
{
  "status": 400,
  "detail": "old_password and new_password are required"
}

// Non-admin trying to change other user's password
{
  "status": 403,
  "detail": "Forbidden"
}
```
### System Status (Admin Only)
- `GET /api/v1/status` - Get system status logs

**Query Parameters:**
- `start_date` - Filter logs from date
- `end_date` - Filter logs until date
- `sort` - Sort by timestamp (default: `-timestamp`)
- `page` - Page number
- `per_page` - Items per page (max: 1000)

**Response Fields:**
- `id` - Unique identifier for the status log entry
- `timestamp` - Date and time when the status was recorded
- `executions_active` - Number of active executions
- `executions_ready` - Number of executions ready to run
- `executions_running` - Number of currently running executions
- `executions_finished` - Number of finished executions
- `executions_failed` - Number of failed executions
- `executions_count` - Total number of executions in the system
- `users_count` - Total number of users in the system
- `scripts_count` - Total number of scripts in the system
- `memory_available_percent` - System memory availability as a percentage
- `cpu_usage_percent` - System CPU usage as a percentage

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
Users with `role: "ADMIN"` or `role: "SUPERADMIN"` can access all functionality without restrictions and can:
- Filter scripts and executions by any user field
- Sort results by user name or email
- Include user names and emails in API responses
- Access user management endpoints

**SuperAdmin Exclusive Access:**
Users with `role: "SUPERADMIN"` have exclusive access to critical user management operations:
- Change user roles (including creating other admins/superadmins)
- Delete other users
- Change other users' passwords
- Update other users' profile information

**Admin Access:**
Users with `role: "ADMIN"` can access most functionality but cannot perform user management operations restricted to superadmins.

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
    "executions_failed": "integer",
    "executions_count": "integer",
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

**Recommended Approach: Use the Test Script**

**Linux/macOS (Bash):**
```bash
# Comprehensive test runner (handles service dependencies automatically)
./run_tests.sh

# Run specific test files or patterns
./run_tests.sh tests/test_smoke.py
./run_tests.sh tests/test_integration.py
./run_tests.sh -k "test_environment"

# Run with pytest options
./run_tests.sh -v --no-cov tests/test_smoke.py
./run_tests.sh -x  # Stop on first failure

# Reset test database before running
./run_tests.sh --reset-db
```

**Windows (PowerShell):**
```powershell
# Comprehensive test runner (handles service dependencies automatically)
.\run_tests.ps1

# Run specific test files or patterns
.\run_tests.ps1 tests/test_smoke.py
.\run_tests.ps1 tests/test_integration.py
.\run_tests.ps1 -k "test_environment"

# Run with pytest options
.\run_tests.ps1 -v --no-cov tests/test_smoke.py
.\run_tests.ps1 -x  # Stop on first failure

# Reset test database before running
.\run_tests.ps1 -ResetDb

# Alternative: Use batch file wrapper
.\run_tests.bat tests/test_smoke.py
```

**Alternative: Manual Test Execution**
```bash
# Start required services first
docker compose -f docker-compose.develop.yml up -d postgres redis

# Wait for services to be ready
sleep 5

# Run all tests via test service
docker compose -f docker-compose.develop.yml run --rm test

# Run tests with specific parameters
docker compose -f docker-compose.develop.yml run --rm test python -m pytest -v tests/

# Clean up when done
docker compose -f docker-compose.develop.yml down
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

#### Test Script Features
```bash
# The run_tests.sh script provides several advantages:

# 1. Automatic dependency management
./run_tests.sh                    # Starts postgres/redis, runs tests, cleans up

# 2. Test database management
./run_tests.sh --reset-db         # Drops and recreates test database

# 3. Flexible test execution
./run_tests.sh tests/test_smoke.py                           # Specific file
./run_tests.sh tests/test_integration.py::TestAPIIntegration # Specific class
./run_tests.sh -v --no-cov tests/test_smoke.py              # With pytest options
./run_tests.sh -x                                           # Stop on first failure

# 4. Service lifecycle management
# - Starts postgres and redis services
# - Waits for services to be ready
# - Creates test database if needed
# - Runs tests with proper environment
# - Stops services on completion
```

#### Test Environment Features
- **Environment Isolation**: `TESTING=true` and `ENVIRONMENT=test` flags
- **Dependency Management**: Tests run with all production dependencies via Poetry
- **Volume Mounting**: Live code access for debugging and development

#### Development Dependencies

The project uses **Poetry** for dependency management:
- `pyproject.toml` - Main dependency configuration
- `poetry.lock` - Locked dependency versions
- Dependencies are installed during Docker image build

### API Documentation

The API documentation is automatically generated and served at `/api/docs/` using Swagger UI.

#### Swagger UI Security

The API documentation uses **locally hosted** Swagger UI resources for maximum security:

**Local Asset Management:**
Swagger UI assets are downloaded and hosted locally to eliminate external dependencies:

```bash
# Download latest Swagger UI assets
python3 scripts/download_swagger_ui.py
```

**Security Benefits:**
- **No External Dependencies**: All resources served from same origin
- **Supply Chain Protection**: No risk of compromised external CDN resources  
- **Simplified CSP**: Content Security Policy doesn't need external domain exceptions
- **Better Performance**: Faster loading without external requests
- **Offline Capability**: Documentation works without internet access

**Asset Location:**
- CSS: `gefapi/static/swagger-ui/swagger-ui.css`
- JavaScript: `gefapi/static/swagger-ui/swagger-ui-bundle.js`
- Download info: `gefapi/static/swagger-ui/download_info.txt`

#### Documentation Generation

The OpenAPI specification (`swagger.json`) and Swagger UI are now generated dynamically at runtime:

**Dynamic Generation:**
- **Real-time**: Always reflects the current code without needing regeneration
- **On-demand**: Generated when accessing `/api/docs/` or `/swagger.json`
- **Self-contained**: No external dependencies or static file management

**Local Development:**
```bash
# Download Swagger UI assets (if not already present)
python3 scripts/download_swagger_ui.py

# Force download (overwrite existing files)
python3 scripts/download_swagger_ui.py --force

# View documentation at: http://localhost:5000/api/docs/
# The swagger.json is generated automatically - no manual steps needed
```

**Accessing Documentation:**
- **Interactive UI**: Visit `/api/docs/` for the full Swagger UI interface
- **Raw Specification**: Visit `/swagger.json` for the OpenAPI spec JSON
```

### Code Structure

```
gefapi/
├── models/          # SQLAlchemy models
├── routes/          # Flask routes and endpoints
├── services/        # Business logic layer
├── tasks/           # Celery background tasks (monitoring, stale cleanup, finished cleanup)
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
- `JWT_SECRET_KEY` - Secret key for JWT tokens (falls back to `SECRET_KEY` if not set)
- `SECRET_KEY` - General secret key for the application
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
- `prod.env` - Production environment settings

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
- **Runs as `gef-api` user for security** (non-root user with Docker socket access)
- Supports multiple entry points via `entrypoint.sh`

#### Security Configuration

**Docker Socket Access**: The application requires Docker socket access for script execution. Security is maintained by:
- Running containers as non-root `gef-api` user
- Adding user to `docker` group for socket access only
- Using `group_add` in docker-compose for proper permissions
- No root privileges for the main application process

**Setup**: Run `./scripts/setup-docker-security.sh` to automatically configure Docker group permissions.

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

## Background Tasks (Celery)

The application uses Celery for background task processing and periodic maintenance tasks. All tasks are automatically managed by the Celery beat scheduler.

### Periodic Tasks

#### System Status Monitoring
- **Task**: `collect_system_status`
- **Schedule**: Every 2 minutes (120 seconds)
- **Purpose**: Collects and stores system metrics including:
  - Execution counts by status (active, ready, running, finished, failed)
  - Total user and script counts
  - System resource usage (CPU, memory)
- **Location**: `gefapi/tasks/status_monitoring.py`
- **Database**: Results stored in `status_log` table
- **Access**: Data accessible via `/api/v1/status` endpoint (Admin only)

#### Stale Execution Cleanup
- **Task**: `cleanup_stale_executions`
- **Schedule**: Every hour (3600 seconds)
- **Purpose**: Maintains system hygiene by:
  - Finding executions older than 3 days that are still running or pending
  - Setting their status to "FAILED"
  - Cleaning up associated Docker services and containers
- **Location**: `gefapi/tasks/execution_cleanup.py`
- **Criteria**: Executions with `start_date` older than 3 days and status not "FINISHED" or "FAILED"
- **Docker Cleanup**: Removes both Docker services and containers named `execution-{execution_id}`

#### Finished Execution Cleanup
- **Task**: `cleanup_finished_executions`
- **Schedule**: Every day (86400 seconds)
- **Purpose**: Maintains resource efficiency by:
  - Finding executions that finished within the past day
  - Cleaning up associated Docker services and containers that may still be running
  - Preventing resource leaks from completed tasks
- **Location**: `gefapi/tasks/execution_cleanup.py`
- **Criteria**: Executions with status "FINISHED" and `end_date` within the past 24 hours
- **Docker Cleanup**: Removes both Docker services and containers named `execution-{execution_id}`

#### Old Failed Execution Cleanup
- **Task**: `cleanup_old_failed_executions`
- **Schedule**: Every day (86400 seconds)
- **Purpose**: Maintains long-term system cleanliness by:
  - Finding failed executions older than 14 days
  - Cleaning up any remaining Docker services and containers from old failed executions
  - Preventing accumulation of Docker resources from historical failures
- **Location**: `gefapi/tasks/execution_cleanup.py`
- **Criteria**: Executions with status "FAILED" and `end_date` older than 14 days
- **Docker Cleanup**: Removes both Docker services and containers named `execution-{execution_id}`

### Manual Task Execution

For development and debugging purposes, tasks can be executed manually:

```bash
# Execute stale execution cleanup
docker exec -it <container_name> celery -A gefapi.celery call gefapi.tasks.execution_cleanup.cleanup_stale_executions

# Execute finished execution cleanup
docker exec -it <container_name> celery -A gefapi.celery call gefapi.tasks.execution_cleanup.cleanup_finished_executions

# Execute old failed execution cleanup
docker exec -it <container_name> celery -A gefapi.celery call gefapi.tasks.execution_cleanup.cleanup_old_failed_executions

# Monitor task execution
docker exec -it <container_name> celery -A gefapi.celery flower
```

### Task Configuration

The Celery beat schedule is configured in `gefapi/celery.py`:

```python
celery.conf.beat_schedule = {
    "cleanup-stale-executions": {
        "task": "gefapi.tasks.execution_cleanup.cleanup_stale_executions", 
        "schedule": 3600.0,  # Every hour
    },
    "cleanup-finished-executions": {
        "task": "gefapi.tasks.execution_cleanup.cleanup_finished_executions",
        "schedule": 86400.0,  # Every day
    },
    "cleanup-old-failed-executions": {
        "task": "gefapi.tasks.execution_cleanup.cleanup_old_failed_executions",
        "schedule": 86400.0,  # Every day
    },
}
```

### Task Monitoring

- **Logs**: All task execution is logged with detailed information
- **Error Handling**: Failed tasks are reported to Rollbar (if configured)
- **Health Checks**: Task failures are tracked and can be monitored via system logs
- **Database Transactions**: All database operations use proper transaction handling

### Docker Service Management

The cleanup task manages Docker resources created during script execution:

- **Service Names**: Docker services are named `execution-{execution_id}`
- **Container Names**: Docker containers are named `execution-{execution_id}`
- **Cleanup Strategy**: 
  - Services are removed using `client.services.get(name).remove()`
  - Containers are removed using `client.containers.get(name).remove(force=True)`
  - Both operations include error handling for missing resources

### Development Notes

- **Environment**: Tasks respect the `ENVIRONMENT` setting and may behave differently in development vs production
- **Docker Availability**: Tasks gracefully handle cases where Docker is not available
- **Database Context**: All tasks run within Flask application context for proper database access
- **Testing**: Tasks can be tested using Celery's eager mode (`CELERY_ALWAYS_EAGER=True`)

## API Documentation

### Recent Improvements

- **Field Exclusion Support**: All serialization methods (`Script`, `User`, `Execution`) now support the `exclude` parameter to remove unwanted fields from API responses, improving performance and reducing payload sizes.
- **Consistent Parameter Handling**: The `include` and `exclude` parameters are now consistently supported across all GET endpoints for scripts, users, and executions.

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
docker compose -f docker-compose.develop.yml logs postgres

# Reset database
docker compose -f docker-compose.develop.yml down -v
docker compose -f docker-compose.develop.yml up postgres
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
docker compose -f docker-compose.develop.yml up -d postgres redis

# Check if test database exists
docker compose -f docker-compose.develop.yml exec postgres psql -U trendsearth_develop -d trendsearth_develop_db -c "\l"

# Create test database manually if needed
docker compose -f docker-compose.develop.yml exec postgres psql -U trendsearth_develop -d trendsearth_develop_db -c "CREATE DATABASE gef_test;"
```

#### Test Environment Variables
```bash
# Verify test.env file exists and is properly configured
cat test.env

# Check environment variables in test container
docker compose -f docker-compose.develop.yml run --rm test env | grep -E "(DATABASE|REDIS|TESTING)"
```

#### Common Test Issues
```bash
# Reset test database completely
./run_tests.sh --reset-db

# Check test service logs
docker compose -f docker-compose.develop.yml logs test

# Verify test service can connect to dependencies
docker compose -f docker-compose.develop.yml run --rm test python -c "
import psycopg2, redis
print('Database connection: OK')
print('Redis connection: OK')
"
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

#### JWT Secret Key Issues

If you see JWT-related errors like `RuntimeError: JWT_SECRET_KEY or flask SECRET_KEY must be set when using symmetric algorithm "HS256"`:

```bash
# Check if JWT_SECRET_KEY or SECRET_KEY is set in your environment file
grep -E "(JWT_SECRET_KEY|SECRET_KEY)" prod.env

# Ensure your environment file is being loaded by the container
docker compose logs <service_name> | grep -i "secret"

# Verify environment variables are available in the container
docker exec -it <container_name> env | grep -E "(JWT_SECRET_KEY|SECRET_KEY)"
```

**Solution**: Add `JWT_SECRET_KEY=your_secure_random_key` to your production environment file (`prod.env`, `staging.env`, etc.).

#### Authentication Token Issues

If you're experiencing authentication issues with the refresh token system:

```bash
# Check if refresh tokens are being created
docker compose logs <service_name> | grep -i "refresh"

# Verify refresh token table exists
docker compose exec database psql -U root -d gefdb -c "\d refresh_tokens"

# Clean up expired tokens manually if needed
docker compose run --rm api python -c "
from gefapi.services.refresh_token_service import RefreshTokenService
print(f'Cleaned up {RefreshTokenService.cleanup_expired_tokens()} expired tokens')
"
```

**Common Issues:**
- **401 Unauthorized**: Access token expired, use refresh token to get a new one
- **Refresh token invalid**: Refresh token expired or revoked, user needs to login again
- **Session not found**: Session was revoked or cleaned up, user needs to login again

For persistent issues, please create an issue in the repository with:
- Error messages and logs
- Operating system and Docker version
- Docker Compose file being used
- Steps to reproduce the problem
