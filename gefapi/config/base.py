from datetime import timedelta
import os

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
        "API_URL": os.getenv("API_URL"),
        "API_USER": os.getenv("API_USER"),
        "API_PASSWORD": os.getenv("API_PASSWORD"),
        "PARAMS_S3_PREFIX": os.getenv("PARAMS_S3_PREFIX"),
        "PARAMS_S3_BUCKET": os.getenv("PARAMS_S3_BUCKET"),
        "CORS_ORIGINS": os.getenv("CORS_ORIGINS"),
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
    "SECRET_KEY": os.getenv("SECRET_KEY", "change-this-in-production"),
    "DOCKER_HOST": os.getenv("DOCKER_HOST"),
    "REGISTRY_URL": os.getenv("REGISTRY_URL"),
    "SCRIPTS_S3_PREFIX": os.getenv("SCRIPTS_S3_PREFIX"),
    "SCRIPTS_S3_BUCKET": os.getenv("SCRIPTS_S3_BUCKET"),
    "PARAMS_S3_PREFIX": os.getenv("PARAMS_S3_PREFIX"),
    "PARAMS_S3_BUCKET": os.getenv("PARAMS_S3_BUCKET"),
    "UPLOAD_FOLDER": "/tmp/scripts",
    "ALLOWED_EXTENSIONS": {"tar.gz"},
    "JWT_ACCESS_TOKEN_EXPIRES": timedelta(seconds=60 * 60 * 2),
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
