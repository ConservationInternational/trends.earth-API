from datetime import timedelta
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_environment_user = os.getenv("API_ENVIRONMENT_USER")
if _environment_user:
    _environment_user = _environment_user.strip().lower()
else:
    _environment_user = "gef@gef.com"


SETTINGS = {
    "logging": {"level": os.getenv("LOG_LEVEL", "INFO")},
    "service": {"port": 3000},
    "environment": {
        "ROLLBAR_SCRIPT_TOKEN": os.getenv("ROLLBAR_SCRIPT_TOKEN")
        or os.getenv("ROLLBAR_SERVER_TOKEN"),
        "GOOGLE_PROJECT_ID": os.getenv("GOOGLE_PROJECT_ID"),
        "GEE_ENDPOINT": os.getenv("GEE_ENDPOINT"),
        "EE_SERVICE_ACCOUNT_JSON": os.getenv("EE_SERVICE_ACCOUNT_JSON"),
        "SPARKPOST_API_KEY": os.getenv("SPARKPOST_API_KEY"),
        "PARAMS_S3_PREFIX": os.getenv("PARAMS_S3_PREFIX"),
        "PARAMS_S3_BUCKET": os.getenv("PARAMS_S3_BUCKET"),
        "CORS_ORIGINS": os.getenv("CORS_ORIGINS"),
        # openEO output bucket – separate from the params bucket so openEO
        # jobs can write GeoTIFF results without commingling with job params.
        "OUTPUT_S3_BUCKET": os.getenv("OUTPUT_S3_BUCKET"),
        "OUTPUT_S3_PREFIX": os.getenv(
            "OUTPUT_S3_PREFIX", "outputs"
        ),  # API configuration required for trends.earth-Environment integration
        "API_ENVIRONMENT_USER": _environment_user,
        "API_ENVIRONMENT_USER_PASSWORD": os.getenv("API_ENVIRONMENT_USER_PASSWORD"),
        # API_URL for execution containers - use internal URL to bypass rate limiting
        "API_URL": os.getenv("API_INTERNAL_URL"),
        # OAuth client credentials for GEE authentication
        "GOOGLE_OAUTH_CLIENT_ID": os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
        "GOOGLE_OAUTH_CLIENT_SECRET": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
        "GOOGLE_OAUTH_TOKEN_URI": os.getenv(
            "GOOGLE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token"
        ),
    },
    # Public API URL for emails (password reset links, etc.)
    # Distinct from SETTINGS["environment"]["API_URL"] (for internal container use)
    "API_PUBLIC_URL": os.getenv("API_PUBLIC_URL"),
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
    # Orchestrator type: "docker" (Docker Swarm, current) or "k8s" (Kubernetes, future)
    "ORCHESTRATOR": os.getenv("ORCHESTRATOR", "docker"),
    "SCRIPTS_S3_PREFIX": os.getenv("SCRIPTS_S3_PREFIX"),
    "SCRIPTS_S3_BUCKET": os.getenv("SCRIPTS_S3_BUCKET"),
    "PARAMS_S3_PREFIX": os.getenv("PARAMS_S3_PREFIX"),
    "PARAMS_S3_BUCKET": os.getenv("PARAMS_S3_BUCKET"),
    # openEO output bucket – separate from the params bucket.
    "OUTPUT_S3_BUCKET": os.getenv("OUTPUT_S3_BUCKET"),
    "OUTPUT_S3_PREFIX": os.getenv("OUTPUT_S3_PREFIX", "outputs"),
    # GCS bucket used for GEE batch export results.  This is the bucket the
    # GEE service agent must have objectCreator access on.
    "GCS_OUTPUT_BUCKET": os.getenv("GCS_OUTPUT_BUCKET", "ldmt"),
    # Default openEO backend URL (per-script override via Script.openeo_backend_url).
    "OPENEO_DEFAULT_BACKEND_URL": os.getenv("OPENEO_DEFAULT_BACKEND_URL"),
    "UPLOAD_FOLDER": os.getenv(
        "UPLOAD_FOLDER", os.path.join(tempfile.gettempdir(), "scripts")
    ),
    "ALLOWED_EXTENSIONS": {"tar.gz"},
    "MAX_RESULTS_SIZE": int(os.getenv("MAX_RESULTS_SIZE", 600000)),  # 600KB default
    # Compression settings
    "ENABLE_REQUEST_COMPRESSION": os.getenv(
        "ENABLE_REQUEST_COMPRESSION", "true"
    ).lower()
    == "true",
    "COMPRESSION_MIN_SIZE": int(os.getenv("COMPRESSION_MIN_SIZE", 1000)),  # 1KB minimum
    "JWT_ACCESS_TOKEN_EXPIRES": timedelta(seconds=60 * 60 * 1),
    "JWT_REFRESH_TOKEN_EXPIRES": timedelta(days=30),  # 30 days for refresh tokens
    "JWT_ALGORITHM": "HS256",  # Explicit algorithm — never rely on library defaults
    "JWT_TOKEN_LOCATION": ["headers"],
    "JWT_IDENTITY_CLAIM": "sub",  # Standard JWT subject claim for identity
    "JWT_BLOCKLIST_ENABLED": True,  # Enable token blocklist for revocation
    "JWT_BLOCKLIST_TOKEN_CHECKS": ["access"],  # Check access tokens against blocklist
    "TRUSTED_PROXY_COUNT": int(os.getenv("TRUSTED_PROXY_COUNT", "0")),
    "INTERNAL_NETWORKS": [
        net.strip().strip("\"'")  # Remove quotes and whitespace
        for net in os.getenv(
            "INTERNAL_NETWORKS", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
        ).split(",")
        if net.strip().strip("\"'")  # Only include non-empty networks after cleaning
    ],
    "MAX_DECOMPRESSED_REQUEST_SIZE": int(
        os.getenv("MAX_DECOMPRESSED_REQUEST_SIZE", 5 * 1024 * 1024)
    ),
    "ENABLE_API_DOCS": os.getenv("ENABLE_API_DOCS", "true").lower() == "true",
    "API_ENVIRONMENT_USER": _environment_user,
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
    # Execution queue configuration
    # Limits concurrent executions per user to prevent API overload.
    # When a user exceeds this limit, new executions are queued (PENDING with
    # queued_at set) and processed FIFO when slots become available.
    # ADMIN and SUPERADMIN users are exempt from this limit.
    "EXECUTION_QUEUE": {
        "ENABLED": os.getenv("EXECUTION_QUEUE_ENABLED", "true").lower() == "true",
        "MAX_CONCURRENT_PER_USER": int(
            os.getenv("MAX_CONCURRENT_EXECUTIONS_PER_USER", "3")
        ),
        "PROCESSOR_INTERVAL_SECONDS": int(os.getenv("QUEUE_PROCESSOR_INTERVAL", "30")),
    },
    # Rate limiting configuration
    # Note: ADMIN and SUPERADMIN users are automatically exempt from all rate limits
    "RATE_LIMITING": {
        "ENABLED": os.getenv("RATE_LIMITING_ENABLED", "true").lower() == "true",
        "STORAGE_URI": os.getenv("RATE_LIMIT_STORAGE_URI") or os.getenv("REDIS_URL"),
        # DEFAULT_LIMITS: Applied automatically to ALL endpoints (global fallback)
        "DEFAULT_LIMITS": [
            s.strip().strip("\"'")
            for s in (
                os.getenv("DEFAULT_LIMITS") or "1000 per hour,100 per minute"
            ).split(",")
            if s.strip().strip("\"'")
        ],
        # API_LIMITS: For specific endpoints needing moderate rate limiting
        # (manual application)
        "API_LIMITS": [
            s.strip().strip("\"'")
            for s in (os.getenv("API_LIMITS") or "100 per hour,20 per minute").split(
                ","
            )
            if s.strip().strip("\"'")
        ],  # API endpoints
        "AUTH_LIMITS": [
            s.strip().strip("\"'")
            for s in (os.getenv("AUTH_LIMITS") or "60 per minute,600 per hour").split(
                ","
            )
            if s.strip().strip("\"'")
        ],  # Stricter for auth - can reduce once refresh tokens are widely used
        "PASSWORD_RESET_LIMITS": [
            s.strip().strip("\"'")
            for s in (
                os.getenv("PASSWORD_RESET_LIMITS") or "3 per hour,1 per minute"
            ).split(",")
            if s.strip().strip("\"'")
        ],  # Very strict for password reset
        "USER_CREATION_LIMITS": [
            s.strip().strip("\"'")
            for s in (os.getenv("USER_CREATION_LIMITS") or "100 per hour").split(",")
            if s.strip().strip("\"'")
        ],  # User registration limits
        "EXECUTION_RUN_LIMITS": [
            s.strip().strip("\"'")
            for s in (
                os.getenv("EXECUTION_RUN_LIMITS") or "10 per minute,40 per hour"
            ).split(",")
            if s.strip().strip("\"'")
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
    # GEE terms enforcement: when enabled, users must accept GEE terms
    # before running scripts that use Google Earth Engine
    "GEE_TERMS_ENFORCEMENT_ENABLED": os.getenv(
        "GEE_TERMS_ENFORCEMENT_ENABLED", "false"
    ).lower()
    == "true",
    # Bulk Email configuration
    # BULK_EMAIL_APPROVED_SENDERS: comma-separated superadmin emails
    # allowed to send bulk emails. Empty = all superadmins may send.
    "BULK_EMAIL_APPROVED_SENDERS": [
        e.strip().lower()
        for e in os.getenv("BULK_EMAIL_APPROVED_SENDERS", "").split(",")
        if e.strip()
    ],
    # BULK_EMAIL_MAX_RECIPIENTS: threshold above which 2FA (OTP) is required
    "BULK_EMAIL_MAX_RECIPIENTS": int(os.getenv("BULK_EMAIL_MAX_RECIPIENTS", "50")),
    # BULK_EMAIL_FROM_EMAIL: SparkPost from_email address for bulk email sends
    "BULK_EMAIL_FROM_EMAIL": os.getenv(
        "BULK_EMAIL_FROM_EMAIL", "noreply@trends.earth"
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

# Check for email configuration
if not os.getenv("SPARKPOST_API_KEY"):
    logger.warning(
        "SPARKPOST_API_KEY is not set. Email functionality will be disabled. "
        "Set SPARKPOST_API_KEY environment variable to enable email notifications."
    )
elif not SETTINGS.get("BULK_EMAIL_APPROVED_SENDERS"):
    raise RuntimeError(
        "BULK_EMAIL_APPROVED_SENDERS must be set when SPARKPOST_API_KEY is "
        "configured. Set it to a comma-separated list of superadmin email "
        "addresses authorised to send bulk emails "
        "(e.g. 'admin@example.com,ops@example.com'). "
        "This prevents any superadmin from triggering bulk email sends via SparkPost."
    )
