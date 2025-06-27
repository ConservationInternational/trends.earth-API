import pytest


@pytest.mark.usefixtures("client", "auth_headers_admin", "admin_user", "regular_user")
class TestUserFilterSort:
    def test_filter_by_role(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?filter=role=USER", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["role"] == "USER"

    def test_filter_by_country_like(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?filter=country like 'Test%'", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json["data"]
        for user in data:
            assert user["country"].startswith("Test")

    def test_sort_by_name_desc(self, client, auth_headers_admin):
        response = client.get("/api/v1/user?sort=name desc", headers=auth_headers_admin)
        assert response.status_code == 200
        data = response.json["data"]
        names = [u["name"] for u in data]
        assert names == sorted(names, reverse=True)

    def test_sort_by_created_at_asc(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?sort=created_at asc", headers=auth_headers_admin
        )
        assert response.status_code == 200
        data = response.json["data"]
        created_ats = [u["created_at"] for u in data]
        assert created_ats == sorted(created_ats)

    def test_pagination(self, client, auth_headers_admin):
        response = client.get(
            "/api/v1/user?page=1&per_page=1", headers=auth_headers_admin
        )
        assert response.status_code == 200
        assert "page" in response.json
        assert "per_page" in response.json
        assert "total" in response.json
        assert response.json["page"] == 1
        assert response.json["per_page"] == 1
        assert response.json["total"] >= 1
        assert len(response.json["data"]) == 1
