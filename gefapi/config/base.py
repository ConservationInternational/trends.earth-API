from datetime import timedelta
import os

SETTINGS = {
    "logging": {"level": "DEBUG"},
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
    },
    "ROLES": ["ADMIN", "USER"],
    "SQLALCHEMY_DATABASE_URI": "postgresql://"
    + os.getenv("DATABASE_ENV_POSTGRES_USER")
    + ":"
    + os.getenv("DATABASE_ENV_POSTGRES_PASSWORD")
    + "@"
    + os.getenv("DATABASE_PORT_5432_TCP_ADDR")
    + ":"
    + os.getenv("DATABASE_PORT_5432_TCP_PORT")
    + "/"
    + os.getenv("DATABASE_ENV_POSTGRES_DB"),
    "SECRET_KEY": "mysecret",
    "DOCKER_HOST": os.getenv("DOCKER_HOST"),
    "REGISTRY_URL": os.getenv("REGISTRY_URL"),
    "SCRIPTS_S3_PREFIX": os.getenv("SCRIPTS_S3_PREFIX"),
    "SCRIPTS_S3_BUCKET": os.getenv("SCRIPTS_S3_BUCKET"),
    "PARAMS_S3_PREFIX": os.getenv("PARAMS_S3_PREFIX"),
    "PARAMS_S3_BUCKET": os.getenv("PARAMS_S3_BUCKET"),
    "UPLOAD_FOLDER": "/tmp/scripts",
    "ALLOWED_EXTENSIONS": {"tar.gz"},
    "JWT_ACCESS_TOKEN_EXPIRES": timedelta(seconds=60 * 60 * 24),
    "JWT_QUERY_STRING_NAME": "token",
    "JWT_TOKEN_LOCATION": ["headers", "query_string"],
    "CELERY_BROKER_URL": "redis://"
    + os.getenv("REDIS_PORT_6379_TCP_ADDR")
    + ":"
    + os.getenv("REDIS_PORT_6379_TCP_PORT"),
    "CELERY_RESULT_BACKEND": "redis://"
    + os.getenv("REDIS_PORT_6379_TCP_ADDR")
    + ":"
    + os.getenv("REDIS_PORT_6379_TCP_PORT"),
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
