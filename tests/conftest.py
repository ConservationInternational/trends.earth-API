"""
Test configuration and fixtures for Trends.Earth API tests
"""

import os
import sys
import tempfile
from unittest.mock import patch

import pytest

# Add the project root to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set environment variables for testing before importing the app
os.environ["ENV"] = "testing"
os.environ["ENVIRONMENT"] = "testing"
os.environ["TESTING"] = "true"

# Set minimal required environment variables for testing if not already set
if not os.environ.get("JWT_SECRET_KEY"):
    os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-for-ci"
if not os.environ.get("SECRET_KEY"):
    os.environ["SECRET_KEY"] = "test-secret-key-for-ci"
if not os.environ.get("API_ENVIRONMENT_USER"):
    os.environ["API_ENVIRONMENT_USER"] = "env-automation@test.com"
if not os.environ.get("API_ENVIRONMENT_USER_PASSWORD"):
    os.environ["API_ENVIRONMENT_USER_PASSWORD"] = "EnvUser1234!"
if not os.environ.get("API_URL"):
    os.environ["API_URL"] = "http://localhost:3000"

# AWS configuration for testing
if not os.environ.get("AWS_ACCESS_KEY_ID"):
    os.environ["AWS_ACCESS_KEY_ID"] = "test_access_key"
if not os.environ.get("AWS_SECRET_ACCESS_KEY"):
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test_secret_key"
if not os.environ.get("AWS_DEFAULT_REGION"):
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
if not os.environ.get("SCRIPTS_S3_BUCKET"):
    os.environ["SCRIPTS_S3_BUCKET"] = "test-scripts-bucket"
if not os.environ.get("PARAMS_S3_BUCKET"):
    os.environ["PARAMS_S3_BUCKET"] = "test-params-bucket"

# Google Earth Engine configuration for testing
if not os.environ.get("GOOGLE_PROJECT_ID"):
    os.environ["GOOGLE_PROJECT_ID"] = "test-gcp-project"
if not os.environ.get("GEE_ENDPOINT"):
    os.environ["GEE_ENDPOINT"] = "https://earthengine-highvolume.googleapis.com"
if not os.environ.get("EE_SERVICE_ACCOUNT_JSON"):
    os.environ["EE_SERVICE_ACCOUNT_JSON"] = "dGVzdA=="  # base64 for "test"

from flask_jwt_extended import create_access_token

from gefapi import app as flask_app
from gefapi import db
from gefapi.config import SETTINGS
from gefapi.models import Execution, Script, StatusLog, User

# Strong password values for test fixtures
STRONG_GENERIC_PASSWORD = "ValidPass123!"
USER_TEST_PASSWORD = "UserPass123!"
ADMIN_TEST_PASSWORD = "AdminPass123!"
SUPERADMIN_TEST_PASSWORD = "SuperAdmin1!"
ENVIRONMENT_USER_TEST_PASSWORD = os.environ["API_ENVIRONMENT_USER_PASSWORD"]
NEW_STRONG_PASSWORD = "NewStrong123!"

ENVIRONMENT_USER_EMAIL = SETTINGS.get("API_ENVIRONMENT_USER")


@pytest.fixture(scope="function")
def app_no_rate_limiting():
    """Create application for testing without rate limiting"""
    # Use environment DATABASE_URL if available (for CI), otherwise use SQLite
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        # Create temporary database file for local testing
        db_fd, db_path = tempfile.mkstemp()
        database_url = f"sqlite:///{db_path}"
    else:
        db_fd = None
        db_path = None

    # Test configuration without rate limiting
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": database_url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "JWT_SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
        "REDIS_URL": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
        "result_backend": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
        "broker_url": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
        "CELERY_ALWAYS_EAGER": True,
        # Disable rate limiting for performance tests
        "RATE_LIMITING": {
            "ENABLED": False,
        },
    }

    app = flask_app

    with app.app_context():
        # Temporarily store original config
        original_config = {}
        for key, value in test_config.items():
            if key in app.config:
                original_config[key] = app.config[key]

        # Apply test config
        app.config.update(test_config)

        # Disable Flask-Limiter for performance tests
        from gefapi import limiter

        original_limiter_enabled = limiter.enabled
        limiter.enabled = False

        try:
            # Enable PostGIS extension for PostgreSQL databases before creating tables
            if "postgresql" in app.config.get("SQLALCHEMY_DATABASE_URI", ""):
                try:
                    with db.engine.connect() as connection:
                        connection.execute(
                            db.text("CREATE EXTENSION IF NOT EXISTS postgis;")
                        )
                        connection.execute(
                            db.text("CREATE EXTENSION IF NOT EXISTS postgis_topology;")
                        )
                        connection.commit()
                except Exception as e:
                    # Log but don't fail if extension creation fails
                    # (might already exist or not needed)
                    print(f"Warning: Could not create PostGIS extensions: {e}")

            db.create_all()
            yield app
        finally:
            # Close any remaining connections
            try:
                db.session.close()
                db.engine.dispose()
            except Exception:
                pass

            # Restore original limiter state
            limiter.enabled = original_limiter_enabled

            # Restore original state
            for key in test_config:
                if key in original_config:
                    app.config[key] = original_config[key]
                elif key in app.config:
                    del app.config[key]

    if db_fd is not None and db_path is not None:
        os.close(db_fd)
        os.unlink(db_path)


@pytest.fixture(scope="function")
def app():
    """Create application for testing"""
    # Use environment DATABASE_URL if available (for CI), otherwise use SQLite
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        # Create temporary database file for local testing
        db_fd, db_path = tempfile.mkstemp()
        database_url = f"sqlite:///{db_path}"
    else:
        db_fd = None
        db_path = None

    # Test configuration
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": database_url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "JWT_SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
        "REDIS_URL": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
        "result_backend": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
        "broker_url": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
        "CELERY_ALWAYS_EAGER": True,
        # Rate limiting configuration for testing
        "RATE_LIMITING": {
            "ENABLED": True,
            # Use in-memory storage for testing instead of Redis
            "STORAGE_URI": "memory://",
            # More lenient for testing
            "DEFAULT_LIMITS": ["200 per hour", "30 per minute"],
            "AUTH_LIMITS": [
                "2 per minute",
                "5 per hour",
            ],  # Very low limits for testing
            "PASSWORD_RESET_LIMITS": ["1 per minute"],  # Very low limit for testing
            "API_LIMITS": ["50 per hour", "5 per minute"],
            "USER_CREATION_LIMITS": ["2 per minute"],  # Very low limit for testing
            "EXECUTION_RUN_LIMITS": ["3 per minute", "10 per hour"],
        },
    }

    app = flask_app

    with app.app_context():
        # Temporarily store original config
        original_config = {}
        for key, value in test_config.items():
            if key in app.config:
                original_config[key] = app.config[key]

        # Apply test config
        app.config.update(test_config)

        # Ensure Flask-Limiter is enabled for rate limiting tests
        from gefapi import limiter
        from gefapi.utils.rate_limiting import reconfigure_limiter_for_testing

        original_limiter_enabled = limiter.enabled
        limiter.enabled = True

        # Reconfigure limiter with test settings
        reconfigure_limiter_for_testing()

        try:
            # Enable PostGIS extension for PostgreSQL databases before creating tables
            if "postgresql" in app.config.get("SQLALCHEMY_DATABASE_URI", ""):
                try:
                    with db.engine.connect() as connection:
                        connection.execute(
                            db.text("CREATE EXTENSION IF NOT EXISTS postgis;")
                        )
                        connection.execute(
                            db.text("CREATE EXTENSION IF NOT EXISTS postgis_topology;")
                        )
                        connection.commit()
                except Exception as e:
                    # Log but don't fail if extension creation fails
                    # (might already exist or not needed)
                    print(f"Warning: Could not create PostGIS extensions: {e}")

            db.create_all()
            yield app
        finally:
            # Close any remaining connections
            try:
                db.session.close()
                db.engine.dispose()
            except Exception:
                pass

            # Restore original limiter state
            limiter.enabled = original_limiter_enabled

            # Restore original state
            for key in test_config:
                if key in original_config:
                    app.config[key] = original_config[key]
                elif key in app.config:
                    del app.config[key]

    if db_fd is not None and db_path is not None:
        os.close(db_fd)
        os.unlink(db_path)


@pytest.fixture(scope="function")
def db_session(app):
    """Creates a new database session for a test."""
    with app.app_context():
        # Start a transaction that can be rolled back
        connection = db.engine.connect()
        transaction = connection.begin()

        # Configure session to use this connection
        db.session.configure(bind=connection)

        try:
            yield db.session
        finally:
            # Always rollback the transaction
            transaction.rollback()
            connection.close()
            db.session.remove()


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create test CLI runner"""
    return app.test_cli_runner()


@pytest.fixture
def admin_user(app):
    """Create admin user for testing"""
    with app.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email="admin@test.com").first()
        if existing_user:
            return existing_user

        user = User(
            email="admin@test.com",
            password=ADMIN_TEST_PASSWORD,
            name="Admin User",
            role="ADMIN",
            country="Test Country",
            institution="Test Institution",
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)  # Ensure user is attached to session
        return user


@pytest.fixture
def regular_user(app):
    """Create regular user for testing"""
    with app.app_context():
        # Check if user already exists
        user = User.query.filter_by(email="user@test.com").first()
        if user:
            # Ensure the existing user has the correct role and password
            user.role = "USER"
            user.password = user.set_password(USER_TEST_PASSWORD)
            # Ensure email notifications are enabled for consistent testing
            user.email_notifications_enabled = True
        else:
            user = User(
                email="user@test.com",
                password=USER_TEST_PASSWORD,
                name="Regular User",
                role="USER",
                country="Test Country",
                institution="Test Institution",
            )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)  # Ensure user is attached to session
        return user


@pytest.fixture
def superadmin_user(app):
    """Create superadmin user for testing"""
    with app.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email="superadmin@test.com").first()
        if existing_user:
            return existing_user

        user = User(
            email="superadmin@test.com",
            password=SUPERADMIN_TEST_PASSWORD,
            name="Super Admin User",
            role="SUPERADMIN",
            country="Test Country",
            institution="Test Institution",
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)  # Ensure user is attached to session
        return user


@pytest.fixture
def environment_user(app):
    """Create the configured automation user for testing"""
    with app.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email=ENVIRONMENT_USER_EMAIL).first()
        if existing_user:
            return existing_user

        user = User(
            email=ENVIRONMENT_USER_EMAIL,
            password=ENVIRONMENT_USER_TEST_PASSWORD,
            name="Automation User",
            role="USER",  # Note: role is USER but should have superadmin privileges
            country="Test Country",
            institution="Test Institution",
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)  # Ensure user is attached to session
        return user


@pytest.fixture
def sample_script(app, regular_user):
    """Create a sample script for testing"""
    with app.app_context():
        # Merge user to avoid DetachedInstanceError
        regular_user = db.session.merge(regular_user)

        # Check if script already exists
        existing_script = Script.query.filter_by(slug="test-script").first()
        if existing_script:
            # Update existing script to ensure it has the correct status
            existing_script.status = "SUCCESS"
            existing_script.public = True
            db.session.add(existing_script)
            db.session.commit()
            return existing_script

        script = Script(
            name="Test Script",
            slug="test-script",
            user_id=regular_user.id,
        )
        script.status = "SUCCESS"
        script.public = True
        script.restricted = False  # Explicitly set to False
        db.session.add(script)
        db.session.commit()
        return script


@pytest.fixture
def sample_execution(app, regular_user, sample_script):
    """Create sample execution for testing"""
    with app.app_context():
        # Merge objects to avoid DetachedInstanceError
        regular_user = db.session.merge(regular_user)
        sample_script = db.session.merge(sample_script)

        # Refresh the script object to ensure it's attached to the current session
        script = Script.query.filter_by(slug="test-script").first()
        if not script:
            script = sample_script

        # Refresh the user object to ensure it's attached to the current session
        user = User.query.filter_by(email=regular_user.email).first()
        if not user:
            user = regular_user

        # Check if execution already exists for this script
        existing_execution = Execution.query.filter_by(
            script_id=script.id, user_id=user.id
        ).first()
        if existing_execution:
            db.session.expunge(existing_execution)
            existing_execution = db.session.merge(existing_execution)
            return existing_execution

        execution = Execution(
            script_id=script.id,
            user_id=user.id,
            params={"test_param": "test_value"},
        )
        execution.status = "FINISHED"
        db.session.add(execution)
        db.session.commit()
        return execution


@pytest.fixture
def sample_status_log(app):
    """Create sample status log for testing"""
    with app.app_context():
        status_log = StatusLog(
            executions_pending=3,
            executions_ready=2,
            executions_running=3,
            executions_finished=100,
            executions_failed=5,
            executions_cancelled=15,
        )
        db.session.add(status_log)
        db.session.commit()
        return status_log


@pytest.fixture
def admin_token(app, admin_user):
    """Generate token for admin user"""
    with app.app_context():
        # Re-query the user to ensure it's attached to the current session
        user = User.query.filter_by(email="admin@test.com").first()
        if not user:
            # If user doesn't exist, merge the fixture user to current session
            user = db.session.merge(admin_user)
        return create_access_token(identity=user.id)


@pytest.fixture
def user_token(app, regular_user):
    """Generate token for regular user"""
    with app.app_context():
        # Re-query the user to ensure it's attached to the current session
        user = User.query.filter_by(email="user@test.com").first()
        if not user:
            # If user doesn't exist, merge the fixture user to current session
            user = db.session.merge(regular_user)
        return create_access_token(identity=user.id)


@pytest.fixture
def superadmin_token(app, superadmin_user):
    """Generate token for superadmin user"""
    with app.app_context():
        # Re-query the user to ensure it's attached to the current session
        user = User.query.filter_by(email="superadmin@test.com").first()
        if not user:
            # If user doesn't exist, merge the fixture user to current session
            user = db.session.merge(superadmin_user)
        return create_access_token(identity=user.id)


@pytest.fixture
def environment_user_token(app, environment_user):
    """Generate token for the automation user"""
    with app.app_context():
        # Re-query the user to ensure it's attached to the current session
        user = User.query.filter_by(email=ENVIRONMENT_USER_EMAIL).first()
        if not user:
            # If user doesn't exist, merge the fixture user to current session
            user = db.session.merge(environment_user)
        return create_access_token(identity=user.id)


@pytest.fixture
def auth_headers_admin(admin_token):
    """Get authorization headers for admin"""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def auth_headers_user(user_token):
    """Get authorization headers for regular user"""
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def auth_headers_superadmin(superadmin_token):
    """Get authorization headers for superadmin user"""
    return {"Authorization": f"Bearer {superadmin_token}"}


@pytest.fixture
def auth_headers_environment_user(environment_user_token):
    """Get authorization headers for the automation user"""
    return {"Authorization": f"Bearer {environment_user_token}"}


@pytest.fixture
def regular_user_no_rate_limiting(app_no_rate_limiting):
    """Create regular user for testing without rate limiting"""
    with app_no_rate_limiting.app_context():
        # Check if user already exists
        user = User.query.filter_by(email="user@test.com").first()
        if user:
            # Ensure the existing user has the correct role and password
            user.role = "USER"
            user.password = user.set_password(USER_TEST_PASSWORD)
            # Ensure email notifications are enabled for consistent testing
            user.email_notifications_enabled = True
        else:
            user = User(
                email="user@test.com",
                password=USER_TEST_PASSWORD,
                name="Regular User",
                role="USER",
                country="Test Country",
                institution="Test Institution",
            )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)  # Ensure user is attached to session
        return user


@pytest.fixture
def admin_user_no_rate_limiting(app_no_rate_limiting):
    """Create admin user for testing without rate limiting"""
    with app_no_rate_limiting.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email="admin@test.com").first()
        if existing_user:
            return existing_user

        user = User(
            email="admin@test.com",
            password=ADMIN_TEST_PASSWORD,
            name="Admin User",
            role="ADMIN",
            country="Test Country",
            institution="Test Institution",
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)  # Ensure user is attached to session
        return user


@pytest.fixture
def client_no_rate_limiting(app_no_rate_limiting):
    """Create test client without rate limiting"""
    return app_no_rate_limiting.test_client()


@pytest.fixture
def auth_headers_user_no_rate_limiting(
    app_no_rate_limiting, regular_user_no_rate_limiting
):
    """Get authorization headers for regular user without rate limiting"""
    with app_no_rate_limiting.app_context():
        # Re-query the user to ensure it's attached to the current session
        user = User.query.filter_by(email="user@test.com").first()
        if not user:
            # If user doesn't exist, merge the fixture user to current session
            user = db.session.merge(regular_user_no_rate_limiting)
        token = create_access_token(identity=user.id)
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers_admin_no_rate_limiting(
    app_no_rate_limiting, admin_user_no_rate_limiting
):
    """Get authorization headers for admin user without rate limiting"""
    with app_no_rate_limiting.app_context():
        # Re-query the user to ensure it's attached to the current session
        user = User.query.filter_by(email="admin@test.com").first()
        if not user:
            # If user doesn't exist, merge the fixture user to current session
            user = db.session.merge(admin_user_no_rate_limiting)
        token = create_access_token(identity=user.id)
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reset_rate_limits(client, superadmin_token):
    """Reset rate limits by calling the reset endpoint."""

    def _reset():
        headers = {"Authorization": f"Bearer {superadmin_token}"}
        response = client.post("/api/v1/rate-limit/reset", headers=headers)
        assert response.status_code == 200

    return _reset


@pytest.fixture
def sample_user_data():
    """Sample user data for testing"""
    import uuid

    unique_id = str(uuid.uuid4())[:8]
    return {
        "email": f"testuser-{unique_id}@test.com",
        "password": STRONG_GENERIC_PASSWORD,
        "name": "Test User",
        "role": "USER",
        "country": "Test Country",
        "institution": "Test Institution",
    }


@pytest.fixture
def sample_script_data():
    """Sample script data for testing"""
    import uuid

    unique_id = str(uuid.uuid4())[:8]
    return {
        "name": f"Test Script for Integration {unique_id}",
        "slug": f"test-script-integration-{unique_id}",
        "description": "A test script for integration testing",
        "public": True,
        "cpu": 1,
        "memory": 1024,
        "uri": "test/script",
    }


@pytest.fixture(autouse=True)
def cleanup_xss_test_users(app):
    """Clean up any XSS test users that might interfere with other tests"""
    with app.app_context():
        # Run after each test to clean up any XSS test data
        yield
        # Clean up users with script content in their names
        xss_patterns = ["<script>", "javascript:", "<img", "<svg"]
        for pattern in xss_patterns:
            users_to_delete = User.query.filter(User.name.like(f"%{pattern}%")).all()
            for user in users_to_delete:
                db.session.delete(user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


@pytest.fixture(autouse=True)
def mock_external_services():
    """Mock external services like S3, email, etc."""
    with (
        patch("gefapi.services.docker_service.docker_build"),
        patch("gefapi.services.docker_service.docker_run"),
        patch("gefapi.services.email_service.EmailService.send_html_email"),
        patch("gefapi.s3.get_script_from_s3"),
        patch("gefapi.s3.push_script_to_s3"),
        patch("gefapi.s3.push_params_to_s3"),
    ):
        yield


@pytest.fixture
def sample_script_file():
    """Create a temporary script file for testing"""
    import io
    import json
    import tarfile
    import uuid

    # Generate unique script name to avoid conflicts
    unique_id = uuid.uuid4().hex[:8]

    # Create the configuration.json content
    config_content = {
        "name": f"Test Script for Integration {unique_id}",
        "cpu_reservation": 100000000,  # 10% of a CPU (1e8)
        "cpu_limit": 500000000,  # 50% of a CPU (5e8)
        "memory_reservation": 104857600,  # 100MB in bytes
        "memory_limit": 1073741824,  # 1GB in bytes
        "environment": "trends.earth-environment",
        "environment_version": "0.1.6",
    }

    # Create a simple test script content
    script_content = """#!/usr/bin/env python3
print("Hello from test script!")
"""

    # Create a tar.gz file in memory
    tar_buffer = io.BytesIO()

    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        # Add configuration.json
        config_json = json.dumps(config_content).encode("utf-8")
        config_info = tarfile.TarInfo("configuration.json")
        config_info.size = len(config_json)
        tar.addfile(config_info, io.BytesIO(config_json))

        # Add the script file
        script_info = tarfile.TarInfo("script.py")
        script_info.size = len(script_content.encode("utf-8"))
        tar.addfile(script_info, io.BytesIO(script_content.encode("utf-8")))

    tar_buffer.seek(0)
    tar_buffer.name = "test_script.tar.gz"
    return tar_buffer


@pytest.fixture
def rate_limiting_enabled(app):
    """Fixture to ensure rate limiting is enabled and working properly in tests"""
    with app.app_context():
        # Clear any existing rate limit state
        from gefapi import limiter
        from gefapi.utils.rate_limiting import reconfigure_limiter_for_testing

        # Ensure limiter is properly configured for testing
        limiter.enabled = True
        reconfigure_limiter_for_testing()

        # Clear storage to start fresh
        try:
            if hasattr(limiter, "_storage"):
                if hasattr(limiter._storage, "storage"):
                    limiter._storage.storage.clear()
                elif hasattr(limiter._storage, "reset"):
                    limiter._storage.reset()
        except Exception:
            pass

        yield

        # Cleanup after test
        try:
            if hasattr(limiter, "_storage"):
                if hasattr(limiter._storage, "storage"):
                    limiter._storage.storage.clear()
                elif hasattr(limiter._storage, "reset"):
                    limiter._storage.reset()
        except Exception:
            pass


@pytest.fixture
def rate_limiting_disabled(app):
    """Fixture to temporarily disable rate limiting for performance tests"""
    with app.app_context():
        from gefapi import limiter

        original_enabled = limiter.enabled
        limiter.enabled = False

        yield

        # Restore original state
        limiter.enabled = original_enabled


# No database cleanup fixture - let tests handle their own state


# Stats testing fixtures


@pytest.fixture(scope="function")
def mock_redis():
    """Create a mock Redis client that properly intercepts cache calls."""
    from unittest.mock import MagicMock, patch

    # Create a comprehensive mock Redis cache
    mock_redis_cache = MagicMock()

    # Configure common Redis cache operations
    mock_redis_cache.is_available.return_value = True
    mock_redis_cache.get.return_value = None  # Cache miss by default
    mock_redis_cache.set.return_value = True
    mock_redis_cache.setex.return_value = True
    mock_redis_cache.delete.return_value = 1
    mock_redis_cache.get_ttl.side_effect = lambda key: {
        "stats_service:get_dashboard_stats:period=all": 240,
        "stats_service:get_execution_stats:period=last_month": 180,
    }.get(key, 300)

    # Mock the underlying client for direct operations
    mock_redis_client = MagicMock()
    mock_redis_cache.client = mock_redis_client
    mock_redis_client.scan_iter.return_value = []
    mock_redis_client.delete.return_value = 0
    mock_redis_client.ping.return_value = True
    mock_redis_client.ttl.return_value = 300

    # Apply patches for both the module-level cache and the getter function
    with (
        patch("gefapi.utils.redis_cache.redis_cache", mock_redis_cache),
        patch(
            "gefapi.utils.redis_cache.get_redis_cache", return_value=mock_redis_cache
        ),
        patch(
            "gefapi.services.stats_service.get_redis_cache",
            return_value=mock_redis_cache,
        ),
    ):
        yield mock_redis_cache


@pytest.fixture(scope="function")
def mock_stats_service():
    """Create a mock StatsService."""
    from unittest.mock import MagicMock, patch

    from gefapi.services.stats_service import StatsService

    with patch.object(StatsService, "__init__", return_value=None):
        service = StatsService()

        # Mock all methods
        service.get_dashboard_stats = MagicMock()
        service.get_execution_stats = MagicMock()
        service.get_user_stats = MagicMock()
        service.get_cache_info = MagicMock()
        service.clear_cache = MagicMock()
        service._get_summary_stats = MagicMock()

        yield service


@pytest.fixture(scope="function")
def sample_execution_data():
    """Create sample execution data for testing."""
    return [
        {
            "id": "exec1",
            "status": "FINISHED",
            "task": "productivity",
            "version": "2",
            "start_date": "2025-08-08T10:00:00Z",
            "end_date": "2025-08-08T10:45:00Z",
            "user_id": "user1",
            "country": "USA",
        },
        {
            "id": "exec2",
            "status": "FAILED",
            "task": "land-cover",
            "version": "2",
            "start_date": "2025-08-08T11:00:00Z",
            "end_date": "2025-08-08T11:30:00Z",
            "user_id": "user2",
            "country": "Brazil",
        },
    ]


@pytest.fixture(scope="function")
def sample_user_stats_data():
    """Create sample user data for stats testing."""
    return [
        {
            "id": "user1",
            "email": "user1@example.com",
            "created_date": "2025-07-01T00:00:00Z",
            "country": "USA",
            "role": "USER",
        },
        {
            "id": "user2",
            "email": "user2@example.com",
            "created_date": "2025-08-01T00:00:00Z",
            "country": "Brazil",
            "role": "USER",
        },
    ]


# Test utilities for stats


def create_cache_key(method_name: str, **kwargs) -> str:
    """Helper function to create cache keys for testing."""
    params = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()) if v is not None)
    return (
        f"stats_service:{method_name}:{params}"
        if params
        else f"stats_service:{method_name}"
    )


def assert_api_response_structure(response_data: dict, expected_keys=None):
    """Assert standard API response structure."""
    if expected_keys is None:
        expected_keys = ["data"]

    for key in expected_keys:
        assert key in response_data, f"Expected key '{key}' in response"


def assert_error_response_structure(response_data: dict):
    """Assert error response structure."""
    # Check for the actual API error format: {'detail': '...', 'status': 400}
    assert "detail" in response_data
    assert "status" in response_data
    assert isinstance(response_data["detail"], str)
    assert isinstance(response_data["status"], int)
    assert response_data["status"] >= 400
