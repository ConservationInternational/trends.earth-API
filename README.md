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

- **Python 3.6+** - Main programming language
- **Flask** - Web framework for API endpoints
- **SQLAlchemy** - ORM for database operations (PostgreSQL)
- **Celery** - Background task management (with Redis)
- **Docker** - Containerization for development and production
- **Gunicorn** - WSGI server for production deployment

## Getting Started

### Requirements

- [Docker](https://www.docker.com/) and Docker Compose
- Git

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
  - Runs on port 5000
  - Command: `./entrypoint.sh start` (uses Gunicorn)

- **`worker`** - Celery worker for background tasks
  - Processes execution jobs and script builds
  - Command: `./entrypoint.sh worker`

- **`beat`** - Celery beat scheduler
  - Manages periodic tasks (system monitoring every 2 minutes)
  - Command: `./entrypoint.sh beat`

### Supporting Services

- **`postgres`** - PostgreSQL database
  - Stores all application data
  - Default port: 5432

- **`redis`** - Redis message broker
  - Handles Celery task queues
  - Default port: 6379

- **`nginx`** (production) - Reverse proxy and load balancer
  - Serves static files and routes requests
  - Handles SSL termination

## Database Management

### Creating Migrations

When you add new fields or modify existing models:

1. **Generate migration:**
   ```bash
   docker exec -it <container_name> flask db migrate -m "Description of changes"
   ```

2. **Apply migration:**
   ```bash
   docker exec -it <container_name> flask db upgrade
   ```

### Maintenance Container

For database operations, use the admin container:

```bash
# Start maintenance container
docker compose -f docker-compose.admin.yml up -d

# Connect to container
docker exec -it trendsearth-api-admin-1 /bin/bash

# Run migrations
flask db migrate -m "Add new field"
flask db upgrade

# Exit and cleanup
exit
docker compose -f docker-compose.admin.yml down
```

## API Endpoints

### Authentication
- `POST /auth` - User authentication

### Scripts
- `GET /api/v1/script` - List all scripts
- `GET /api/v1/script/<script_id>` - Get specific script
- `POST /api/v1/script` - Create new script
- `PATCH /api/v1/script/<script_id>` - Update script
- `DELETE /api/v1/script/<script_id>` - Delete script (Admin only)
- `POST /api/v1/script/<script_id>/publish` - Publish script
- `POST /api/v1/script/<script_id>/unpublish` - Unpublish script
- `GET /api/v1/script/<script_id>/download` - Download script
- `GET /api/v1/script/<script_id>/log` - Get script logs

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
- `start_date_gte` - Filter executions started after date
- `start_date_lte` - Filter executions started before date
- `end_date_gte` - Filter executions ended after date
- `end_date_lte` - Filter executions ended before date
- `sort` - Sort results (supports: `status`, `start_date`, `end_date`, `duration`, `script_name`, `user_name`)
- `include` - Include additional data (`duration`, `user`, `script`, `logs`)
- `exclude` - Exclude data (`params`, `results`)
- `page` - Page number (default: 1)
- `per_page` - Items per page (default: 20, max: 100)

**Examples:**
```bash
# Get finished executions with duration, sorted by longest first
GET /api/v1/execution?status=FINISHED&include=duration&sort=-duration

# Get executions from last week, sorted by script name
GET /api/v1/execution?start_date_gte=2025-06-14&sort=script_name&include=script

# Get running executions with user info
GET /api/v1/execution?status=RUNNING&include=user,duration&sort=-start_date
```

### Users
- `GET /api/v1/user` - List all users (Admin only)
- `GET /api/v1/user/<user_id>` - Get specific user (Admin only)
- `GET /api/v1/user/me` - Get current user profile
- `POST /api/v1/user` - Create new user
- `PATCH /api/v1/user/<user_id>` - Update user (Admin only)
- `PATCH /api/v1/user/me` - Update own profile
- `DELETE /api/v1/user/<user_id>` - Delete user (Admin only)
- `DELETE /api/v1/user/me` - Delete own account
- `POST /api/v1/user/<user_id>/recover-password` - Password recovery

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

```bash
# Start all services
docker compose -f docker-compose.develop.yml up

# Start specific service
docker compose -f docker-compose.develop.yml up api

# View logs
docker compose -f docker-compose.develop.yml logs -f api

# Run tests
docker compose -f docker-compose.develop.yml exec api python -m pytest
```

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

### Production Deployment

1. **Build and push image:**
   ```bash
   docker build -t registry.example.com/trendsearth-api .
   docker push registry.example.com/trendsearth-api
   ```

2. **Deploy with Docker Swarm:**
   ```bash
   docker stack deploy -c docker-compose.prod.yml api
   ```

### Environment Variables

Key environment variables to configure:

- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `JWT_SECRET_KEY` - Secret key for JWT tokens
- `SMTP_*` - Email configuration
- `AWS_*` - S3 bucket configuration

## Monitoring

The system automatically collects metrics every 2 minutes:
- Execution status counts
- User and script totals
- System resource usage (CPU, memory)

Access monitoring data via the `/api/v1/status` endpoint (Admin only).

## API Documentation

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

## License

This project is licensed under the MIT License - see the LICENSE file for details.
