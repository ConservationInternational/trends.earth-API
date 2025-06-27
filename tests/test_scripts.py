import pytest


@pytest.mark.usefixtures("client", "auth_headers_user", "sample_script")
class TestScriptFilterSort:
    def test_filter_by_status(self, client, auth_headers_user, sample_script):
        response = client.get(
            "/api/v1/script?filter=status=SUCCESS", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        for script in data:
            assert script["status"] == "SUCCESS"

    def test_filter_by_public(self, client, auth_headers_user, sample_script):
        response = client.get(
            "/api/v1/script?filter=public=true", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        for script in data:
            assert script["public"] is True

    def test_filter_by_multiple(self, client, auth_headers_user, sample_script):
        response = client.get(
            "/api/v1/script?filter=status=SUCCESS,public=true",
            headers=auth_headers_user,
        )
        assert response.status_code == 200
        data = response.json["data"]
        for script in data:
            assert script["status"] == "SUCCESS"
            assert script["public"] is True

    def test_sort_by_name_desc(self, client, auth_headers_user, sample_script):
        response = client.get(
            "/api/v1/script?sort=name desc", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        names = [s["name"] for s in data]
        assert names == sorted(names, reverse=True)

    def test_sort_by_created_at_asc(self, client, auth_headers_user, sample_script):
        response = client.get(
            "/api/v1/script?sort=created_at asc", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        created_ats = [s["created_at"] for s in data]
        assert created_ats == sorted(created_ats)

    def test_pagination(self, client, auth_headers_user, sample_script):
        response = client.get(
            "/api/v1/script?page=1&per_page=1", headers=auth_headers_user
        )
        assert response.status_code == 200
        assert "page" in response.json
        assert "per_page" in response.json
        assert "total" in response.json
        assert response.json["page"] == 1
        assert response.json["per_page"] == 1
        assert response.json["total"] >= 1
        assert len(response.json["data"]) == 1
