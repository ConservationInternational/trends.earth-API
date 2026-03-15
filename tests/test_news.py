"""
Tests for the News API endpoints
"""

import datetime
import json

from flask_jwt_extended import create_access_token
import pytest

from gefapi import db
from gefapi.models.news import NewsItem


@pytest.fixture
def news_item(app, admin_user):
    """Create a sample news item for testing"""
    with app.app_context():
        # Merge admin user to avoid DetachedInstanceError
        admin_user = db.session.merge(admin_user)

        news = NewsItem(
            title="Test News Item",
            message="This is a test news message",
            link_url="https://example.com/news",
            link_text="Read more",
            target_platforms="app,webapp,api-ui",
            is_active=True,
            priority=5,
            news_type="announcement",
            created_by_id=str(admin_user.id),
        )
        db.session.add(news)
        db.session.commit()
        db.session.refresh(news)
        return news


@pytest.fixture
def inactive_news_item(app, admin_user):
    """Create an inactive news item for testing"""
    with app.app_context():
        admin_user = db.session.merge(admin_user)

        news = NewsItem(
            title="Inactive News Item",
            message="This is an inactive news item",
            target_platforms="app",
            is_active=False,
            priority=0,
            news_type="announcement",
            created_by_id=str(admin_user.id),
        )
        db.session.add(news)
        db.session.commit()
        db.session.refresh(news)
        return news


@pytest.fixture
def expired_news_item(app, admin_user):
    """Create an expired news item for testing"""
    with app.app_context():
        admin_user = db.session.merge(admin_user)

        news = NewsItem(
            title="Expired News Item",
            message="This is an expired news item",
            target_platforms="app,webapp",
            is_active=True,
            priority=0,
            news_type="announcement",
            expires_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
            created_by_id=str(admin_user.id),
        )
        db.session.add(news)
        db.session.commit()
        db.session.refresh(news)
        return news


@pytest.fixture
def version_specific_news_item(app, admin_user):
    """Create a version-specific news item for testing"""
    with app.app_context():
        admin_user = db.session.merge(admin_user)

        news = NewsItem(
            title="Version Specific News",
            message="Only for versions 2.0.0 to 3.0.0",
            target_platforms="app",
            is_active=True,
            priority=10,
            news_type="release",
            min_version="2.0.0",
            max_version="3.0.0",
            created_by_id=str(admin_user.id),
        )
        db.session.add(news)
        db.session.commit()
        db.session.refresh(news)
        return news


class TestGetNews:
    """Tests for GET /api/v1/news endpoint"""

    def test_get_news_no_auth(self, client, app, news_item):
        """Test that news can be retrieved without authentication"""
        with app.app_context():
            response = client.get("/api/v1/news")
            assert response.status_code == 200
            data = json.loads(response.data)
            assert "data" in data
            assert "page" in data
            assert "total" in data

    def test_get_news_with_auth(self, client, app, regular_user, news_item):
        """Test news retrieval with authentication"""
        with app.app_context():
            regular_user = db.session.merge(regular_user)
            token = create_access_token(identity=regular_user.id)

            response = client.get(
                "/api/v1/news", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200
            data = json.loads(response.data)
            assert "data" in data
            assert len(data["data"]) > 0

    def test_get_news_filter_by_platform(self, client, app, news_item, admin_user):
        """Test filtering news by platform"""
        with app.app_context():
            # Create a news item only for webapp
            admin_user = db.session.merge(admin_user)
            webapp_only_news = NewsItem(
                title="Webapp Only News",
                message="Only for webapp",
                target_platforms="webapp",
                is_active=True,
                created_by_id=str(admin_user.id),
            )
            db.session.add(webapp_only_news)
            db.session.commit()

            # Filter for app platform
            response = client.get("/api/v1/news?platform=app")
            assert response.status_code == 200
            data = json.loads(response.data)

            # Should include news_item (app,webapp,api-ui) but not webapp_only
            titles = [item["title"] for item in data["data"]]
            assert "Test News Item" in titles

    def test_get_news_excludes_inactive(
        self, client, app, news_item, inactive_news_item
    ):
        """Test that inactive news items are excluded"""
        with app.app_context():
            response = client.get("/api/v1/news")
            assert response.status_code == 200
            data = json.loads(response.data)

            titles = [item["title"] for item in data["data"]]
            assert "Test News Item" in titles
            assert "Inactive News Item" not in titles

    def test_get_news_excludes_expired(self, client, app, news_item, expired_news_item):
        """Test that expired news items are excluded"""
        with app.app_context():
            response = client.get("/api/v1/news")
            assert response.status_code == 200
            data = json.loads(response.data)

            titles = [item["title"] for item in data["data"]]
            assert "Test News Item" in titles
            assert "Expired News Item" not in titles

    def test_get_news_pagination(self, client, app, admin_user):
        """Test news pagination"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)

            # Create multiple news items
            for i in range(5):
                news = NewsItem(
                    title=f"Paginated News {i}",
                    message=f"Paginated message {i}",
                    is_active=True,
                    created_by_id=str(admin_user.id),
                )
                db.session.add(news)
            db.session.commit()

            # Get first page with 2 items
            response = client.get("/api/v1/news?page=1&per_page=2")
            assert response.status_code == 200
            data = json.loads(response.data)
            assert len(data["data"]) == 2
            assert data["page"] == 1
            assert data["per_page"] == 2


class TestGetSingleNewsItem:
    """Tests for GET /api/v1/news/<news_id> endpoint"""

    def test_get_single_news_item(self, client, app, news_item):
        """Test retrieving a single news item"""
        with app.app_context():
            news_item = db.session.merge(news_item)
            response = client.get(f"/api/v1/news/{news_item.id}")
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["data"]["title"] == "Test News Item"

    def test_get_single_news_item_not_found(self, client, app):
        """Test retrieving a non-existent news item"""
        with app.app_context():
            response = client.get("/api/v1/news/00000000-0000-0000-0000-000000000000")
            assert response.status_code == 404


class TestAdminNewsEndpoints:
    """Tests for admin news management endpoints"""

    def test_admin_get_news_includes_inactive(
        self, client, app, admin_user, news_item, inactive_news_item
    ):
        """Test that admin endpoint includes inactive items"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            token = create_access_token(identity=admin_user.id)

            response = client.get(
                "/api/v1/admin/news", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200
            data = json.loads(response.data)

            titles = [item["title"] for item in data["data"]]
            assert "Test News Item" in titles
            assert "Inactive News Item" in titles

    def test_admin_get_news_forbidden_for_regular_user(
        self, client, app, regular_user, news_item
    ):
        """Test that regular users cannot access admin endpoint"""
        with app.app_context():
            regular_user = db.session.merge(regular_user)
            token = create_access_token(identity=regular_user.id)

            response = client.get(
                "/api/v1/admin/news", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 403

    def test_create_news_item(self, client, app, admin_user):
        """Test creating a news item"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            token = create_access_token(identity=admin_user.id)

            response = client.post(
                "/api/v1/admin/news",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(
                    {
                        "title": "New Announcement",
                        "message": "Important announcement content",
                        "link_url": "https://example.com",
                        "target_platforms": "app,webapp",
                        "priority": 10,
                        "news_type": "announcement",
                    }
                ),
            )
            assert response.status_code == 201
            data = json.loads(response.data)
            assert data["data"]["title"] == "New Announcement"
            assert data["data"]["priority"] == 10

    def test_create_news_item_missing_required_fields(self, client, app, admin_user):
        """Test that creating news item requires title and message"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            token = create_access_token(identity=admin_user.id)

            response = client.post(
                "/api/v1/admin/news",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps({"title": "Only Title"}),
            )
            assert response.status_code == 400

    def test_create_news_item_forbidden_for_regular_user(
        self, client, app, regular_user
    ):
        """Test that regular users cannot create news items"""
        with app.app_context():
            regular_user = db.session.merge(regular_user)
            token = create_access_token(identity=regular_user.id)

            response = client.post(
                "/api/v1/admin/news",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(
                    {
                        "title": "Unauthorized News",
                        "message": "Should not be created",
                    }
                ),
            )
            assert response.status_code == 403

    def test_update_news_item(self, client, app, admin_user, news_item):
        """Test updating a news item"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            news_item = db.session.merge(news_item)
            token = create_access_token(identity=admin_user.id)

            response = client.put(
                f"/api/v1/admin/news/{news_item.id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(
                    {
                        "title": "Updated Title",
                        "priority": 20,
                    }
                ),
            )
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["data"]["title"] == "Updated Title"
            assert data["data"]["priority"] == 20

    def test_update_news_item_not_found(self, client, app, admin_user):
        """Test updating a non-existent news item"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            token = create_access_token(identity=admin_user.id)

            response = client.put(
                "/api/v1/admin/news/00000000-0000-0000-0000-000000000000",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps({"title": "Updated Title"}),
            )
            assert response.status_code == 404

    def test_delete_news_item(self, client, app, admin_user, news_item):
        """Test deleting a news item"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            news_item = db.session.merge(news_item)
            news_id = str(news_item.id)
            token = create_access_token(identity=admin_user.id)

            response = client.delete(
                f"/api/v1/admin/news/{news_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

            # Verify deletion
            deleted_news = NewsItem.query.filter_by(id=news_id).first()
            assert deleted_news is None

    def test_delete_news_item_not_found(self, client, app, admin_user):
        """Test deleting a non-existent news item"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            token = create_access_token(identity=admin_user.id)

            response = client.delete(
                "/api/v1/admin/news/00000000-0000-0000-0000-000000000000",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 404


class TestNewsVersionFiltering:
    """Tests for version-based news filtering"""

    def test_version_in_range(self, client, app, version_specific_news_item):
        """Test that version in range returns the news item"""
        with app.app_context():
            response = client.get("/api/v1/news?version=2.5.0")
            assert response.status_code == 200
            data = json.loads(response.data)

            titles = [item["title"] for item in data["data"]]
            assert "Version Specific News" in titles

    def test_version_below_range(self, client, app, version_specific_news_item):
        """Test that version below range excludes the news item"""
        with app.app_context():
            response = client.get("/api/v1/news?version=1.0.0")
            assert response.status_code == 200
            data = json.loads(response.data)

            titles = [item["title"] for item in data["data"]]
            assert "Version Specific News" not in titles

    def test_version_above_range(self, client, app, version_specific_news_item):
        """Test that version above range excludes the news item"""
        with app.app_context():
            response = client.get("/api/v1/news?version=4.0.0")
            assert response.status_code == 200
            data = json.loads(response.data)

            titles = [item["title"] for item in data["data"]]
            assert "Version Specific News" not in titles


class TestNewsTypes:
    """Tests for different news types"""

    def test_create_news_with_warning_type(self, client, app, admin_user):
        """Test creating a warning-type news item"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            token = create_access_token(identity=admin_user.id)

            response = client.post(
                "/api/v1/admin/news",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(
                    {
                        "title": "Warning Message",
                        "message": "This is a warning",
                        "news_type": "warning",
                    }
                ),
            )
            assert response.status_code == 201
            data = json.loads(response.data)
            assert data["data"]["news_type"] == "warning"

    def test_create_news_with_invalid_type(self, client, app, admin_user):
        """Test that invalid news type is rejected"""
        with app.app_context():
            admin_user = db.session.merge(admin_user)
            token = create_access_token(identity=admin_user.id)

            response = client.post(
                "/api/v1/admin/news",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(
                    {
                        "title": "Invalid Type News",
                        "message": "This has invalid type",
                        "news_type": "invalid_type",
                    }
                ),
            )
            assert response.status_code == 400
