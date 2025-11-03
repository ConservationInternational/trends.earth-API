# Copilot Instructions for Trends.Earth API

## Repository Overview

**Trends.Earth API** is a comprehensive Flask-based Python API that manages Scripts, Users, and Executions for the Trends.Earth project. It serves as the backend for both the Trends.Earth QGIS plugin and web interfaces.

### High-Level Information
- **Language**: Python 3.11+ (Alpine-based Docker containers)
- **Framework**: Flask with SQLAlchemy ORM
- **Database**: PostgreSQL with Flask-Migrate migrations
- **Background Tasks**: Celery with Redis message broker
- **Dependency Management**: Poetry with locked dependencies (`poetry.lock`)
- **Code Quality**: Ruff for linting and formatting
- **Testing**: Pytest with 263 test functions across 24 test files
- **Containerization**: Docker with multi-service architecture
- **CI/CD**: GitHub Actions for testing, linting, and deployment

### Related Projects
- [Trends.Earth CLI](https://github.com/conservationinternational/trends.earth-CLI) - Command Line Interface
- [Trends.Earth Core Environment](https://github.com/conservationinternational/trends.earth-Environment) - Execution environment
- [Trends.Earth UI](https://github.com/conservationinternational/trends.earth-UI) - Web interface

## Pre-installed Environment (GitHub Copilot Agents)

**IMPORTANT**: If you are a GitHub Copilot agent, the development environment has been pre-configured via `copilot-setup-steps.yml`. The following dependencies and tools are **already installed** - do NOT attempt to reinstall them as this may cause firewall restrictions or conflicts:

### Pre-installed System Dependencies
- **Docker** (latest version with Docker Compose plugin)
- **Python 3.11+** with pip and venv support
- **Poetry** dependency manager (latest version)
- **Build tools**: git, curl, jq, build-essential, ca-certificates

### Pre-pulled Docker Images
- `postgres:16` (database)
- `redis:latest` (Celery message broker) 
- `registry:2.8.1` (local Docker registry)
- `python:3.11-alpine` (API base image)

### Pre-configured Environment
- **Environment files**: `develop.env` and `test.env` are auto-generated from `.env.example`
- **Project dependencies**: All Poetry dependencies (including dev dependencies) are installed
- **Docker containers**: All containers from `docker-compose.develop.yml` are pre-built
- **Database**: PostgreSQL and Redis services are validated and ready

### Ready-to-Use Commands
Since the environment is pre-configured, you can immediately use:
```bash
# Start development environment (no build needed)
docker-compose -f docker-compose.develop.yml up

# Run tests (all dependencies ready)
./run_tests.sh

# Code linting (Poetry and tools ready)
poetry run ruff check gefapi/ tests/

# API documentation (services ready)
# Available at: http://localhost:3000/api/docs/
```

**Do not run**: `poetry install`, `docker pull`, `pip install`, or system package installation commands. The environment is complete and ready for development.

## Build and Validation Instructions

### Prerequisites
- Docker and Docker Compose
- Git
- **Windows users**: WSL2 recommended for optimal performance

### Quick Start Workflow

#### 1. Environment Setup
**ALWAYS create environment files before building** (these files are gitignored):
```bash
# Copy and customize environment configuration
cp .env.example develop.env
cp .env.example test.env

# Minimal development environment file (develop.env):
cat > develop.env << EOF
ENVIRONMENT=dev
DEBUG=True
TESTING=false
JWT_SECRET_KEY=dev-jwt-secret-key-change-in-production
SECRET_KEY=dev-secret-key-change-in-production
DATABASE_URL=postgresql://trendsearth_develop:postgres@postgres:5432/trendsearth_develop_db
REDIS_URL=redis://redis:6379/0
API_ENVIRONMENT_USER=dev_user
API_ENVIRONMENT_USER_PASSWORD=dev_password
API_URL=http://localhost:3000
RATE_LIMITING_ENABLED=true
RATE_LIMIT_STORAGE_URI=redis://redis:6379/1
EOF

# Minimal test environment file (test.env):
cat > test.env << EOF
ENVIRONMENT=test
TESTING=true
JWT_SECRET_KEY=test-jwt-secret-key-for-ci
SECRET_KEY=test-secret-key-for-ci
DATABASE_URL=postgresql://trendsearth_develop:postgres@postgres:5432/gef_test
REDIS_URL=redis://redis:6379/2
RATE_LIMITING_ENABLED=true
RATE_LIMIT_STORAGE_URI=memory://
EOF
```

#### 2. Docker Build and Development
```bash
# Build all services (includes automatic dependency installation via Poetry)
docker compose -f docker-compose.develop.yml build

# Start development environment (includes automatic database migration)
docker compose -f docker-compose.develop.yml up

# The API will be available at http://localhost:3000
# Interactive API docs at http://localhost:3000/api/docs/
```

#### 3. Testing
> **Windows requirement:** Always execute tests via `./run_tests.ps1`. This script provisions Docker containers, databases, and environment variables. Do **not** run `pytest` directly on Windows; those commands will fail because the required services are absent.
**ALWAYS use the test script for reliable results:**
```bash
# Run all tests (recommended approach - handles service dependencies)
./run_tests.sh

# Run specific test files
./run_tests.sh tests/test_smoke.py
./run_tests.sh tests/test_integration.py

# Run with pytest options
./run_tests.sh -v --no-cov tests/test_smoke.py
./run_tests.sh -x  # Stop on first failure

# Alternative: Manual test execution (requires services to be running)
docker compose -f docker-compose.develop.yml up -d postgres redis
docker compose -f docker-compose.develop.yml run --rm test
```

#### 4. Code Quality and Linting

**⚠️ MANDATORY: All code changes MUST pass Ruff linting and formatting without any errors before submission.**

```bash
# Install Poetry locally for linting (if needed)
pip install poetry
poetry install --with dev

# Run Ruff linting (MUST complete without any errors)
poetry run ruff check gefapi/ tests/
poetry run ruff format --check gefapi/ tests/

# Fix formatting issues
poetry run ruff format gefapi/ tests/

# Run comprehensive linting with all rules
poetry run ruff check gefapi/ tests/ --select=E,W,F,UP,B,SIM,I,N,S,C4,PIE,T20,RET,TCH
```

**Important**: `ruff format` and `ruff check` must complete without any errors before submitting code changes.

### Container Commands and Services

#### Available Services (via entrypoint.sh)
```bash
# Development server (Flask with auto-reload)
docker compose -f docker-compose.develop.yml run --rm api develop

# Production server (Gunicorn)
docker compose -f docker-compose.develop.yml run --rm api start

# Background worker (Celery)
docker compose -f docker-compose.develop.yml run --rm api worker

# Scheduler (Celery beat - periodic tasks)
docker compose -f docker-compose.develop.yml run --rm api beat

# Database migrations (automatic in docker-compose)
docker compose -f docker-compose.develop.yml run --rm api migrate

# Run tests
docker compose -f docker-compose.develop.yml run --rm api test
```

#### Docker Compose Environments
- **`docker-compose.develop.yml`** - Development with live code reloading
- **`docker-compose.staging.yml`** - Production-like staging environment  
- **`docker-compose.prod.yml`** - Production deployment
- **`docker-compose.admin.yml`** - Administrative tasks and manual operations

### Database Management

#### Automatic Migrations
Database migrations run automatically when using `docker-compose.develop.yml up` via the `migrate` service.

#### Manual Migration Operations
```bash
# Start admin container for manual operations
docker compose -f docker-compose.admin.yml up -d

# Generate new migration
docker exec -it trendsearth-api-admin-1 flask db migrate -m "Description of changes"

# Apply migrations manually (usually not needed)
docker exec -it trendsearth-api-admin-1 flask db upgrade

# Cleanup
docker compose -f docker-compose.admin.yml down
```

**CRITICAL - Migration Naming Requirements**: 
- **Revision IDs**: ALL Alembic migration revision IDs MUST be 12-character hexadecimal hashes (e.g., `3eedf39b54dd`, `7b6a9c8d5e4f`). Never use descriptive names, letters beyond a-f, or non-hex characters.
- **File Names**: Migration files MUST follow the format `{revision_id}_{description}.py` (e.g., `3eedf39b54dd_add_build_error_column.py`)
- **Validation**: Run the migration chain analyzer to verify proper naming before committing
- **Consequences**: Improper revision IDs cause Alembic KeyError exceptions and break the migration system

**Example of CORRECT migration naming:**
```python
# File: 3eedf39b54dd_add_user_column.py
revision = '3eedf39b54dd'  # ✅ Correct: 12-char hex hash
down_revision = '2b4c6d8e0a1f'  # ✅ Correct: references proper hash

# File naming: ✅ CORRECT
3eedf39b54dd_add_user_column.py

# File naming: ❌ INCORRECT
add_user_column.py
user_migration_2025.py
```

### Common Issues and Solutions

#### Docker Build Failures
- **Network issues**: Alpine package manager may fail due to network restrictions
- **Docker socket access**: Required for script execution functionality
- **Permission issues**: Use `./scripts/setup-docker-security.sh` on Linux/WSL

#### Environment Variables Missing
- **JWT_SECRET_KEY**: Required for authentication - create environment files first
- **DATABASE_URL**: Must point to accessible PostgreSQL instance
- **TESTING=true**: Required for test environment

#### Test Database Issues
```bash
# Create test database manually if needed
docker compose -f docker-compose.develop.yml exec postgres psql -U trendsearth_develop -d trendsearth_develop_db -c "CREATE DATABASE gef_test;"
```

### Performance Considerations
- **Test execution**: ~263 tests take 2-5 minutes depending on environment
- **Docker build**: First build takes 5-10 minutes due to Poetry dependency installation
- **Linting**: Ruff is fast (~10 seconds for entire codebase)

## Project Structure and Architecture

### Core Application Structure
```
gefapi/                 # Main application package
├── models/            # SQLAlchemy models (User, Script, Execution, StatusLog)
├── routes/            # Flask routes and API endpoints
├── services/          # Business logic layer
├── tasks/             # Celery background tasks (monitoring, cleanup)
├── config/            # Configuration files (base.py, prod.py, staging.py)
├── validators/        # Request validation
├── static/            # Static assets (Swagger UI)
├── utils/             # Utility functions
└── errors.py          # Custom exceptions
```

### Configuration Files
- **`pyproject.toml`** - Poetry dependencies and Ruff configuration
- **`pytest.ini`** - Test configuration with markers and timeouts
- **`pylintrc`** - Legacy linting configuration (replaced by Ruff)
- **`.pre-commit-config.yaml`** - Pre-commit hooks for code quality

### Docker Architecture
- **API Container**: Flask application (development/production modes)
- **Worker Container**: Celery background task processor
- **Beat Container**: Celery scheduler for periodic tasks
- **Migrate Container**: Database schema migrations
- **PostgreSQL**: Primary database (version 16)
- **Redis**: Message broker and caching

### Key Files
- **`main.py`** - Application entry point for local development
- **`entrypoint.sh`** - Docker container entry point with multiple commands
- **`run_tests.sh`** - Test execution script with service management
- **`run_db_migrations.py`** - Database migration script
- **`gunicorn.py`** - Production WSGI server configuration

### CI/CD Pipeline

#### GitHub Actions Workflows
- **`.github/workflows/run-tests.yml`** - Comprehensive test suite (Python 3.11, 3.12)
- **`.github/workflows/ruff.yaml`** - Code quality checks
- **`.github/workflows/deploy-staging.yml`** - Staging deployment
- **`.github/workflows/deploy-production.yml`** - Production deployment

#### Validation Steps
1. **Ruff linting**: Code quality and formatting checks
2. **Comprehensive testing**: All 263 tests across multiple Python versions
3. **Integration testing**: Full workflow validation
4. **Security scanning**: Safety and Bandit security checks
5. **Coverage reporting**: Code coverage analysis

### API Documentation
- **Interactive docs**: Available at `/api/docs/` (Swagger UI)
- **OpenAPI spec**: Auto-generated at `/swagger.json`
- **Local assets**: Swagger UI assets hosted locally for security

### Background Tasks (Celery)
- **System monitoring**: Every 2 minutes (resource usage, execution counts)
- **Stale execution cleanup**: Every hour (cleanup >3 day old running executions)
- **Finished execution cleanup**: Daily (cleanup completed task containers)
- **Old failed execution cleanup**: Daily (cleanup >14 day old failed executions)

## Development Guidelines

### Code Style

**⚠️ CRITICAL REQUIREMENT: `ruff format` and `ruff check` must complete without any errors before submitting code changes. This is strictly enforced.**

- **Ruff formatting**: 88 character line length, double quotes
- **Import sorting**: isort-compatible with known first-party packages
- **Error handling**: Comprehensive exception handling with proper logging
- **Security**: Bandit security scanning, input validation
- **Quality requirements**: All code must pass Ruff linting and formatting checks

### Database Patterns
- **SQLAlchemy ORM**: Declarative models in `gefapi/models/`
- **Flask-Migrate**: Database versioning and migrations
- **Transactions**: Proper rollback handling in services

### Testing Patterns
- **Pytest fixtures**: Comprehensive app and database fixtures in `conftest.py`
- **Test categories**: Unit, integration, performance, security
- **Markers**: `@pytest.mark.slow`, `@pytest.mark.integration`, etc.
- **Mock patterns**: Database and external service mocking

### Environment Configuration
- **Environment files**: `develop.env`, `test.env`, `staging.env`, `prod.env` (gitignored)
- **Configuration classes**: Base, development, staging, production configs
- **Feature flags**: Rate limiting, debug mode, testing mode

**Note**: Environment files (*.env) are gitignored and must be created from `.env.example` for each deployment.

## Key Commands Reference

### Development Workflow
```bash
# Complete development setup
cp .env.example develop.env  # Edit as needed
docker compose -f docker-compose.develop.yml build
docker compose -f docker-compose.develop.yml up

# Code quality checks
poetry run ruff check gefapi/ tests/
poetry run ruff format gefapi/ tests/

# Testing
./run_tests.sh                           # All tests
./run_tests.sh tests/test_smoke.py       # Specific file
./run_tests.sh -v -x                    # Verbose, stop on failure

# Database operations
docker compose -f docker-compose.admin.yml up -d
docker exec -it trendsearth-api-admin-1 flask db migrate -m "Change description"
docker compose -f docker-compose.admin.yml down
```

### Validation Commands
```bash
# Verify setup
poetry run python -c "import gefapi; print('✅ App imports successfully')"
docker compose -f docker-compose.develop.yml config  # Validate compose files

# REQUIRED: Code quality validation (MUST pass before submitting)
poetry run ruff check gefapi/ tests/                 # Full linting check
poetry run ruff format --check gefapi/ tests/        # Formatting check
poetry run ruff check gefapi/ --select=E,W,F         # Basic linting

# Test connectivity
curl http://localhost:3000/api-health                # Health check
curl http://localhost:3000/api/docs/                 # API documentation
```

### Troubleshooting
```bash
# View logs
docker compose -f docker-compose.develop.yml logs -f api
docker compose -f docker-compose.develop.yml logs -f worker

# Reset environment
docker compose -f docker-compose.develop.yml down -v
docker compose -f docker-compose.develop.yml build --no-cache
docker compose -f docker-compose.develop.yml up

# Database issues
docker compose -f docker-compose.develop.yml exec postgres psql -U trendsearth_develop -d trendsearth_develop_db
```

## Trust These Instructions

These instructions have been validated against the actual codebase and reflect the current working setup. The commands listed here work as documented. Only search for additional information if:

1. **Commands fail with unexpected errors** not covered in troubleshooting
2. **Environment-specific issues** arise in your particular setup
3. **New features** require information not covered here
4. **Instructions appear outdated** based on recent changes to the codebase

The repository includes comprehensive documentation in the README.md, but these instructions provide the essential operational knowledge for efficient development and testing.