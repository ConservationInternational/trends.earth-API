"""
Test configuration and fixtures for Trends.Earth API tests
"""

import os
import sys
import tempfile
from unittest.mock import patch

# Add the project root to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from gefapi import app as flask_app
from gefapi import db
from gefapi.models import Execution, Script, StatusLog, User


@pytest.fixture(scope="session")
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
    }

    app = flask_app
    app.config.update(test_config)

    with app.app_context():
        db.create_all()
        yield app
        # Close any remaining connections instead of dropping all tables
        try:
            db.session.close()
            db.engine.dispose()
        except Exception:
            pass
    if db_fd is not None and db_path is not None:
        os.close(db_fd)
        os.unlink(db_path)


@pytest.fixture(scope="function")
def db_session(app):
    """
    Yield a database session for a single test.
    """
    with app.app_context():
        connection = db.engine.connect()
        transaction = connection.begin()
        
        # Use the existing session but with a new transaction
        session = db.session
        
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
            connection.close()


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
            password="admin123",
            name="Admin User",
            role="ADMIN",
            country="Test Country",
            institution="Test Institution",
        )
        db.session.add(user)
        db.session.commit()
        return user


@pytest.fixture
def regular_user(app):
    """Create regular user for testing"""
    with app.app_context():
        # Check if user already exists
        existing_user = User.query.filter_by(email="user@test.com").first()
        if existing_user:
            return existing_user

        user = User(
            email="user@test.com",
            password="user123",
            name="Regular User",
            role="USER",
            country="Test Country",
            institution="Test Institution",
        )
        db.session.add(user)
        db.session.commit()
        return user


@pytest.fixture
def admin_token(client, admin_user):
    """Get JWT token for admin user"""
    response = client.post(
        "/auth", json={"email": "admin@test.com", "password": "admin123"}
    )
    assert response.status_code == 200, f"Authentication failed: {response.get_json()}"
    data = response.get_json()
    assert "access_token" in data, f"No access_token in response: {data}"
    return data["access_token"]


@pytest.fixture
def user_token(client, regular_user):
    """Get JWT token for regular user"""
    response = client.post(
        "/auth", json={"email": "user@test.com", "password": "user123"}
    )
    assert response.status_code == 200, f"Authentication failed: {response.get_json()}"
    data = response.get_json()
    assert "access_token" in data, f"No access_token in response: {data}"
    return data["access_token"]


@pytest.fixture
def sample_script(app, regular_user):
    """Create sample script for testing"""
    with app.app_context():
        # Check if script already exists
        existing_script = Script.query.filter_by(slug="test-script").first()
        if existing_script:
            return existing_script

        script = Script(
            name="Test Script",
            slug="test-script",
            user_id=regular_user.id,
        )
        script.status = "SUCCESS"
        script.public = True
        db.session.add(script)
        db.session.commit()
        return script


@pytest.fixture
def sample_execution(app, regular_user, sample_script):
    """Create sample execution for testing"""
    with app.app_context():
        # Check if execution already exists for this script
        existing_execution = Execution.query.filter_by(
            script_id=sample_script.id, user_id=regular_user.id
        ).first()
        if existing_execution:
            return existing_execution

        execution = Execution(
            script_id=sample_script.id,
            user_id=regular_user.id,
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
            executions_active=5,
            executions_ready=2,
            executions_running=3,
            executions_finished=100,
            users_count=10,
            scripts_count=25,
            memory_available_percent=75.5,
            cpu_usage_percent=25.0,
        )
        db.session.add(status_log)
        db.session.commit()
        return status_log


@pytest.fixture
def auth_headers_admin(admin_token):
    """Authorization headers for admin user"""
    if admin_token is None:
        pytest.fail("Admin token is None - authentication failed")
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def auth_headers_user(user_token):
    """Authorization headers for regular user"""
    if user_token is None:
        pytest.fail("User token is None - authentication failed")
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def sample_user_data():
    """Sample user data for testing"""
    import uuid

    unique_id = str(uuid.uuid4())[:8]
    return {
        "email": f"testuser-{unique_id}@test.com",
        "password": "password",
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
def cleanup_db_connections(app):
    """Ensure database connections are properly closed after tests"""
    yield
    # Force close any remaining database connections and cleanup test data conflicts
    with app.app_context():
        try:
            # Clean up any duplicate test data that might cause conflicts
            db.session.rollback()
            db.session.close()
            db.engine.dispose()
        except Exception:
            pass


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


@pytest.fixture(autouse=True)
def cleanup_integration_test_data(app):
    """Clean up integration test data before and after each test"""
    with app.app_context():
        # Clean up before test
        from gefapi import db
        from gefapi.models import Execution, Script, User

        try:
            # Remove any test users that might conflict
            test_users = User.query.filter(
                User.email.like("%testuser-%@test.com")
            ).all()
            for user in test_users:
                # Clean up related data first
                user_executions = Execution.query.filter_by(user_id=user.id).all()
                for execution in user_executions:
                    db.session.delete(execution)
                user_scripts = Script.query.filter_by(user_id=user.id).all()
                for script in user_scripts:
                    db.session.delete(script)
                db.session.delete(user)

            # Remove any test scripts that might conflict
            test_scripts = Script.query.filter(
                Script.name.like("%Test Script for Integration%")
            ).all()
            for script in test_scripts:
                # Clean up related executions first
                script_executions = Execution.query.filter_by(script_id=script.id).all()
                for execution in script_executions:
                    db.session.delete(execution)
                db.session.delete(script)

            db.session.commit()
        except Exception:
            db.session.rollback()

        yield

        # Clean up after test
        try:
            test_users = User.query.filter(
                User.email.like("%testuser-%@test.com")
            ).all()
            for user in test_users:
                user_executions = Execution.query.filter_by(user_id=user.id).all()
                for execution in user_executions:
                    db.session.delete(execution)
                user_scripts = Script.query.filter_by(user_id=user.id).all()
                for script in user_scripts:
                    db.session.delete(script)
                db.session.delete(user)

            test_scripts = Script.query.filter(
                Script.name.like("%Test Script for Integration%")
            ).all()
            for script in test_scripts:
                script_executions = Execution.query.filter_by(script_id=script.id).all()
                for execution in script_executions:
                    db.session.delete(execution)
                db.session.delete(script)

            db.session.commit()
        except Exception:
            db.session.rollback()


# No database cleanup fixture - let tests handle their own state
