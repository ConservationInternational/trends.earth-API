from datetime import timedelta
import os
import logging

logger = logging.getLogger(__name__)

SETTINGS = {
    "logging": {"level": os.getenv("LOG_LEVEL", "INFO")},
    "service": {"port": 3000},
    "environment": {
        "ROLLBAR_SCRIPT_TOKEN": os.getenv("ROLLBAR_SCRIPT_TOKEN"),
        "ROLLBAR_SERVER_TOKEN": os.getenv("ROLLBAR_SERVER_TOKEN"),
        "GOOGLE_PROJECT_ID": os.getenv("GOOGLE_PROJECT_ID"),
        "GEE_ENDPOINT": os.getenv("GEE_ENDPOINT"),
        "EE_SERVICE_ACCOUNT_JSON": os.getenv("EE_SERVICE_ACCOUNT_JSON"),
        "SPARKPOST_API_KEY": os.getenv("SPARKPOST_API_KEY"),
        "PARAMS_S3_PREFIX": os.getenv("PARAMS_S3_PREFIX"),
        "PARAMS_S3_BUCKET": os.getenv("PARAMS_S3_BUCKET"),
        "CORS_ORIGINS": os.getenv("CORS_ORIGINS"),
        # API configuration required for trends.earth-Environment integration
        "API_USER": os.getenv("API_USER"),
        "API_PASSWORD": os.getenv("API_PASSWORD"),
        "API_URL": os.getenv("API_URL"),
    },
    "ROLES": ["SUPERADMIN", "ADMIN", "USER"],
    "SQLALCHEMY_DATABASE_URI": os.getenv("DATABASE_URL")
    or (
        "postgresql://"
        + (os.getenv("DATABASE_ENV_POSTGRES_USER") or "postgres")
        + ":"
        + (os.getenv("DATABASE_ENV_POSTGRES_PASSWORD") or "postgres")
        + "@"
        + (os.getenv("DATABASE_PORT_5432_TCP_ADDR") or "localhost")
        + ":"
        + (os.getenv("DATABASE_PORT_5432_TCP_PORT") or "5432")
        + "/"
        + (os.getenv("DATABASE_ENV_POSTGRES_DB") or "postgres")
    ),
    "SECRET_KEY": os.getenv("SECRET_KEY"),
    "JWT_SECRET_KEY": os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY"),
    "DOCKER_HOST": os.getenv("DOCKER_HOST"),
    "REGISTRY_URL": os.getenv("REGISTRY_URL"),
    "SCRIPTS_S3_PREFIX": os.getenv("SCRIPTS_S3_PREFIX"),
    "SCRIPTS_S3_BUCKET": os.getenv("SCRIPTS_S3_BUCKET"),
    "PARAMS_S3_PREFIX": os.getenv("PARAMS_S3_PREFIX"),
    "PARAMS_S3_BUCKET": os.getenv("PARAMS_S3_BUCKET"),
    "UPLOAD_FOLDER": "/tmp/scripts",
    "ALLOWED_EXTENSIONS": {"tar.gz"},
    "JWT_ACCESS_TOKEN_EXPIRES": timedelta(
        seconds=60 * 60 * 1
    ),  # Reduced to 1 hour with refresh tokens
    "JWT_REFRESH_TOKEN_EXPIRES": timedelta(days=30),  # 30 days for refresh tokens
    "JWT_TOKEN_LOCATION": ["headers"],
    "CELERY_BROKER_URL": os.getenv("REDIS_URL")
    or (
        "redis://"
        + (os.getenv("REDIS_PORT_6379_TCP_ADDR") or "localhost")
        + ":"
        + (os.getenv("REDIS_PORT_6379_TCP_PORT") or "6379")
    ),
    "CELERY_RESULT_BACKEND": os.getenv("REDIS_URL")
    or (
        "redis://"
        + (os.getenv("REDIS_PORT_6379_TCP_ADDR") or "localhost")
        + ":"
        + (os.getenv("REDIS_PORT_6379_TCP_PORT") or "6379")
    ),
    # Celery also expects lowercase versions
    "broker_url": os.getenv("REDIS_URL")
    or (
        "redis://"
        + (os.getenv("REDIS_PORT_6379_TCP_ADDR") or "localhost")
        + ":"
        + (os.getenv("REDIS_PORT_6379_TCP_PORT") or "6379")
    ),
    "result_backend": os.getenv("REDIS_URL")
    or (
        "redis://"
        + (os.getenv("REDIS_PORT_6379_TCP_ADDR") or "localhost")
        + ":"
        + (os.getenv("REDIS_PORT_6379_TCP_PORT") or "6379")
    ),
    # Rate limiting configuration
    # Note: ADMIN and SUPERADMIN users are automatically exempt from all rate limits
    "RATE_LIMITING": {
        "ENABLED": os.getenv("RATE_LIMITING_ENABLED", "true").lower() == "true",
        "STORAGE_URI": os.getenv("RATE_LIMIT_STORAGE_URI") or os.getenv("REDIS_URL"),
        # DEFAULT_LIMITS: Applied automatically to ALL endpoints (global fallback)
        "DEFAULT_LIMITS": [
            s.strip()
            for s in (
                os.getenv("DEFAULT_LIMITS") or "1000 per hour,100 per minute"
            ).split(",")
        ],
        # API_LIMITS: For specific endpoints needing moderate rate limiting
        # (manual application)
        "API_LIMITS": [
            s.strip()
            for s in (os.getenv("API_LIMITS") or "100 per hour,20 per minute").split(
                ","
            )
        ],  # API endpoints
        "AUTH_LIMITS": [
            s.strip()
            for s in (os.getenv("AUTH_LIMITS") or "60 per minute,600 per hour").split(
                ","
            )
        ],  # Stricter for auth - can reduce once refresh tokens are widely used
        "PASSWORD_RESET_LIMITS": [
            s.strip()
            for s in (
                os.getenv("PASSWORD_RESET_LIMITS") or "10 per hour,3 per minute"
            ).split(",")
        ],  # Very strict for password reset
        "USER_CREATION_LIMITS": [
            s.strip()
            for s in (os.getenv("USER_CREATION_LIMITS") or "100 per hour").split(",")
        ],  # User registration limits
        "EXECUTION_RUN_LIMITS": [
            s.strip()
            for s in (
                os.getenv("EXECUTION_RUN_LIMITS") or "10 per minute,40 per hour"
            ).split(",")
        ],  # Script execution limits
    },
    # Testing configuration for rate limiting
    "TESTING_RATE_LIMITING": {
        "BYPASS_FOR_TESTING": os.getenv(
            "BYPASS_RATE_LIMITING_IN_TESTS", "false"
        ).lower()
        == "true",
        "RESET_BETWEEN_TESTS": os.getenv(
            "RESET_RATE_LIMITS_BETWEEN_TESTS", "true"
        ).lower()
        == "true",
    },
}


def _add_aws_env_var(variable):
    if "environment" not in SETTINGS:
        SETTINGS["environment"] = {}

    SETTINGS["environment"][variable] = os.getenv(variable)


if os.getenv("AWS_ACCESS_KEY_ID"):
    _add_aws_env_var("AWS_ACCESS_KEY_ID")
if os.getenv("AWS_SECRET_ACCESS_KEY"):
    _add_aws_env_var("AWS_SECRET_ACCESS_KEY")
if os.getenv("AWS_DEFAULT_REGION"):
    _add_aws_env_var("AWS_DEFAULT_REGION")

# Check for email configuration
if not os.getenv("SPARKPOST_API_KEY"):
    logger.warning("SPARKPOST_API_KEY is not set. Email functionality will be disabled. Set SPARKPOST_API_KEY environment variable to enable email notifications.")
