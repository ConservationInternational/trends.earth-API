# Trends.Earth API

[![Trends.Earth](https://s3.amazonaws.com/trends.earth/sharing/trends_earth_logo_bl_600width.png)](http://trends.earth)

[![Tests](https://github.com/ConservationInternational/trends.earth-API/actions/workflows/run-tests.yml/badge.svg)](https://github.com/ConservationInternational/trends.earth-API/actions/workflows/run-tests.yml)
[![Code Quality](https://github.com/ConservationInternational/trends.earth-API/actions/workflows/ruff.yaml/badge.svg)](https://github.com/ConservationInternational/trends.earth-API/actions/workflows/ruff.yaml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

The Trends.Earth API manages Scripts, Users, and Executions for the
[Trends.Earth](https://trends.earth/) platform. It serves as the backend for the
Trends.Earth QGIS plugin and web interface.

## Technology Stack

- **Python 3.11** — Flask application, Alpine-based Docker image
- **PostgreSQL** — Primary database (SQLAlchemy + Flask-Migrate)
- **Redis + Celery** — Background task processing and scheduling
- **Docker / Docker Swarm** — Containerisation and production deployment
- **Gunicorn** — WSGI server for production
- **Poetry** — Dependency management

## Getting Started

### Requirements

- [Docker](https://www.docker.com/) and Docker Compose
- Git

### Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/ConservationInternational/trends.earth-API
   cd trends.earth-API
   ```

2. **Set up environment files** (required — these files are gitignored):
   ```bash
   cp .env.example develop.env
   cp .env.example test.env
   ```

3. **Build and start development services:**
   ```bash
   docker compose -f docker-compose.develop.yml build
   docker compose -f docker-compose.develop.yml up
   ```

4. **Run tests:**
   ```bash
   ./run_tests.sh        # Linux/macOS
   .\run_tests.ps1       # Windows PowerShell
   ```

The API will be available at `http://localhost:3000`.
Interactive API docs are at `http://localhost:3000/api/docs/`.

## Documentation

Full documentation is in the [`docs/`](docs/) directory:

- [API Reference](docs/api-reference.md) — Endpoints, filtering, sorting, and pagination
- [Authentication](docs/authentication.md) — JWT tokens, refresh tokens, session management
- [Data Models](docs/data-models.md) — Script, Execution, User, and StatusLog schemas
- [Background Tasks](docs/background-tasks.md) — Celery tasks and Docker service monitoring
- [Development Guide](docs/development.md) — Local setup, testing, migrations, and troubleshooting
- [Deployment Guide](docs/deployment/README.md) — Production deployment on AWS

## Contributing

Contributions are welcome. Please report bugs or suggest improvements via the
[issue tracker](https://github.com/ConservationInternational/trends.earth-API/issues).

## Related Projects

`Trends.Earth` is built from a set of interconnected repositories:

- [trends.earth](https://github.com/ConservationInternational/trends.earth) — QGIS plugin for land degradation monitoring
- [trends.earth-schemas](https://github.com/ConservationInternational/trends.earth-schemas) — Data schemas for analysis results
- [trends.earth-algorithms](https://github.com/ConservationInternational/trends.earth-algorithms) — Core analysis algorithms
- [trends.earth-Environment](https://github.com/ConservationInternational/trends.earth-Environment) — Job execution environment for running scripts
- [trends.earth-CLI](https://github.com/ConservationInternational/trends.earth-CLI) — Command-line interface for developing custom scripts
- [trends.earth-api-ui](https://github.com/ConservationInternational/trends.earth-api-ui) — Web UI for API management

## License

MIT License — see [LICENSE](LICENSE).