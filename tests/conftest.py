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
from werkzeug.security import generate_password_hash

from gefapi import app as flask_app
from gefapi import db
from gefapi.models import Execution, Script, StatusLog, User


@pytest.fixture(scope="session")
def app():
    """Create application for testing"""
    # Create temporary database file
    db_fd, db_path = tempfile.mkstemp()

    # Test configuration
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "JWT_SECRET_KEY": "test-secret-key",
        "WTF_CSRF_ENABLED": False,
        "REDIS_URL": "redis://localhost:6379/1",  # Use different Redis DB for tests
        "result_backend": "redis://localhost:6379/1",
        "broker_url": "redis://localhost:6379/1",
    }

    app = flask_app
    app.config.update(test_config)

    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()

    os.close(db_fd)
    os.unlink(db_path)


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
        user = User(
            email="admin@test.com",
            password=generate_password_hash("admin123"),
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
        user = User(
            email="user@test.com",
            password=generate_password_hash("user123"),
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
    assert response.status_code == 200
    return response.json["access_token"]


@pytest.fixture
def user_token(client, regular_user):
    """Get JWT token for regular user"""
    response = client.post(
        "/auth", json={"email": "user@test.com", "password": "user123"}
    )
    assert response.status_code == 200
    return response.json["access_token"]


@pytest.fixture
def sample_script(app, regular_user):
    """Create sample script for testing"""
    with app.app_context():
        script = Script(
            name="Test Script",
            slug="test-script",
            user_id=regular_user.id,
            status="SUCCESS",
            public=True,
        )
        db.session.add(script)
        db.session.commit()
        return script


@pytest.fixture
def sample_execution(app, regular_user, sample_script):
    """Create sample execution for testing"""
    with app.app_context():
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
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def auth_headers_user(user_token):
    """Authorization headers for regular user"""
    return {"Authorization": f"Bearer {user_token}"}


# Mock external services for testing
@pytest.fixture(autouse=True)
def mock_external_services():
    """Mock external services like S3, email, etc."""
    with (
        patch("gefapi.services.docker_service.docker_build"),
        patch("gefapi.services.docker_service.docker_run"),
        patch("gefapi.services.email_service.EmailService.send_html_email"),
        patch("gefapi.s3.get_script_from_s3"),
        patch("gefapi.s3.upload_script_to_s3"),
    ):
        yield
