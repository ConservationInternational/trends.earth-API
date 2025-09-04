"""Configuration for testing environment"""

import os

SETTINGS = {
    # Override database URL for testing - use local PostgreSQL
    "SQLALCHEMY_DATABASE_URI": os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db"),
    
    # Testing flags
    "testing": True,
    "TESTING": True,
    "DEBUG": False,
    
    # Rate limiting configuration for testing
    "RATE_LIMITING": {
        "ENABLED": os.getenv("RATE_LIMITING_ENABLED", "true").lower() == "true",
        # Use in-memory storage for testing instead of Redis
        "STORAGE_URI": os.getenv("RATE_LIMIT_STORAGE_URI", "memory://"),
        "DEFAULT_LIMITS": ["100 per hour", "10 per minute"],
        "AUTH_LIMITS": ["2 per minute", "5 per hour"],  # Very low limits for testing
        "PASSWORD_RESET_LIMITS": ["1 per minute"],  # Very low limit for testing
        "API_LIMITS": ["50 per hour", "5 per minute"],
        "USER_CREATION_LIMITS": ["2 per minute"],  # Very low limit for testing
        "EXECUTION_RUN_LIMITS": ["3 per minute", "10 per hour"],
    },
    
    # Redis configuration for testing - fallback to localhost
    "CELERY_BROKER_URL": os.getenv("REDIS_URL", "redis://localhost:6379/2"),
    "CELERY_RESULT_BACKEND": os.getenv("REDIS_URL", "redis://localhost:6379/2"),
    "broker_url": os.getenv("REDIS_URL", "redis://localhost:6379/2"),
    "result_backend": os.getenv("REDIS_URL", "redis://localhost:6379/2"),
}