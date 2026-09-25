# Development Guide

## Prerequisites

- [Docker](https://www.docker.com/) and Docker Compose
- Git
- Windows users: WSL2 recommended for optimal Docker performance

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/ConservationInternational/trends.earth-API
   cd trends.earth-API
   ```

2. **Create environment files** (required — these are gitignored):
   ```bash
   cp .env.example develop.env
   cp .env.example test.env
   ```
   Edit each file with appropriate values. Minimum required fields:
   - `JWT_SECRET_KEY` and `SECRET_KEY`
   - `DATABASE_URL`
   - `REDIS_URL`

3. **Build and start services:**
   ```bash
   docker compose -f docker-compose.develop.yml build
   docker compose -f docker-compose.develop.yml up
   ```

The API will be available at `http://localhost:3000`.
Interactive API docs are at `http://localhost:3000/api/docs/`.

## Running Tests

Use the provided test scripts, which handle service dependencies automatically:

```bash
# Linux/macOS
./run_tests.sh                           # All tests
./run_tests.sh tests/test_smoke.py       # Specific file
./run_tests.sh -v -x                     # Verbose, stop on first failure
./run_tests.sh --reset-db                # Reset test database first
```

```powershell
# Windows PowerShell
.\run_tests.ps1
.\run_tests.ps1 tests/test_smoke.py
.\run_tests.ps1 -v -x
.\run_tests.ps1 -ResetDb
```

## Container Commands

The `entrypoint.sh` script provides a unified interface:

| Command | Purpose |
|---------|---------|
| `develop` | Flask development server with auto-reload |
| `start` | Gunicorn production server |
| `worker` | Celery worker process |
| `beat` | Celery beat scheduler |
| `migrate` | Database migrations |
| `test` | Test suite execution |

```bash
docker compose -f docker-compose.develop.yml run --rm api develop
docker compose -f docker-compose.develop.yml run --rm api migrate
docker compose -f docker-compose.develop.yml run --rm api test
```

## Code Quality

```bash
# Install Poetry locally for linting
pip install poetry
poetry install --with dev

# Lint (must pass)
poetry run ruff check gefapi/ tests/
poetry run ruff format --check gefapi/ tests/

# Auto-fix
poetry run ruff check --fix gefapi/ tests/
poetry run ruff format gefapi/ tests/
```

## Database Migrations

### Creating a New Migration

```bash
# Start admin container
docker compose -f docker-compose.admin.yml up -d

# Generate migration (uses 12-char hex revision ID)
docker exec -it trendsearth-api-admin-1 flask db migrate -m "Description of change"

# Apply migration manually if needed
docker exec -it trendsearth-api-admin-1 flask db upgrade

# Clean up
docker compose -f docker-compose.admin.yml down
```

**Critical:** All Alembic revision IDs must be 12-character hexadecimal strings
(e.g., `3eedf39b54dd`). Never use descriptive names. Verify the `down_revision` points
to the current HEAD before creating a new migration:

```powershell
# Find the current HEAD revision (the one not referenced as down_revision by any other file)
Select-String -Path "migrations\versions\*.py" -Pattern "^revision = " | Select-Object -Last 5
```

## Code Structure

```
gefapi/
├── models/          # SQLAlchemy models (User, Script, Execution, StatusLog)
├── routes/          # Flask route handlers
├── services/        # Business logic layer
├── tasks/           # Celery background tasks
├── config/          # Configuration classes (base, prod, staging)
├── utils/           # Utility helpers
├── validators.py    # Request validation
├── celery.py        # Celery app and beat schedule
├── s3.py            # S3 helpers
└── errors.py        # Custom exceptions
```

## Environment Variables

### Application

| Variable | Description | Default |
|----------|-------------|---------|
| `JWT_SECRET_KEY` | JWT signing key (falls back to `SECRET_KEY`) | — |
| `SECRET_KEY` | General app secret key | — |
| `ENVIRONMENT` | Runtime environment (`dev`, `test`, `staging`, `prod`) | — |
| `DEBUG` | Debug mode | `False` |
| `TESTING` | Test mode | `False` |
| `PORT` | Application port | `3000` |

### Database and Redis

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials |
| `REDIS_URL` | Redis connection string |

### API URLs

| Variable | Purpose |
|----------|---------|
| `API_PUBLIC_URL` | External-facing URL (used in password reset emails) |
| `API_INTERNAL_URL` | Internal URL for execution containers (bypasses rate limiting) |

### Rate Limiting

| Variable | Description | Default |
|----------|-------------|---------|
| `RATE_LIMITING_ENABLED` | Enable/disable rate limiting | `true` |
| `RATE_LIMIT_STORAGE_URI` | Storage backend (`redis://...` or `memory://`) | — |

## Production Deployment

```bash
# Build and push image
docker build -t registry.example.com/trendsearth-api .
docker push registry.example.com/trendsearth-api

# Deploy with Docker Swarm
docker stack deploy -c docker-compose.prod.yml api
```

The production image runs as non-root `gef-api` user. The user is added to the `docker`
group inside the Dockerfile (GID 999); the entrypoint script checks Docker socket
accessibility at startup and warns if the socket is not writable.

For full deployment documentation, see [deployment/README.md](deployment/README.md).

## Troubleshooting

### JWT_SECRET_KEY missing

```bash
# Add to your environment file
JWT_SECRET_KEY=your_secure_random_key
```

### Test database creation

```bash
docker compose -f docker-compose.develop.yml exec postgres \
  psql -U trendsearth_develop -d trendsearth_develop_db \
  -c "CREATE DATABASE gef_test;"
```

### Reset development environment

```bash
docker compose -f docker-compose.develop.yml down -v
docker compose -f docker-compose.develop.yml build --no-cache
docker compose -f docker-compose.develop.yml up
```

### View logs

```bash
docker compose -f docker-compose.develop.yml logs -f api
docker compose -f docker-compose.develop.yml logs -f worker
```
