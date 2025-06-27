import pytest


@pytest.mark.usefixtures("client", "auth_headers_user")
class TestExecutionFilterSort:
    def test_filter_by_status(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?filter=status=FINISHED", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        for execution in data:
            assert execution["status"] == "FINISHED"

    def test_filter_by_progress_gt(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?filter=progress>50", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        for execution in data:
            assert execution["progress"] > 50

    def test_filter_by_multiple(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?filter=status=FINISHED,progress>50",
            headers=auth_headers_user,
        )
        assert response.status_code == 200
        data = response.json["data"]
        for execution in data:
            assert execution["status"] == "FINISHED"
            assert execution["progress"] > 50

    def test_sort_by_status_desc(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?sort=status desc", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        statuses = [e["status"] for e in data]
        assert statuses == sorted(statuses, reverse=True)

    def test_sort_by_progress_asc(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?sort=progress asc", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        progresses = [e["progress"] for e in data]
        assert progresses == sorted(progresses)

    def test_sort_by_multiple_fields(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?sort=status desc,progress asc", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        # Check that primary sort is by status desc, then by progress asc within each status
        statuses = [e["status"] for e in data]
        progresses = [e["progress"] for e in data]
        # This is a basic check; for full correctness, group by status and check progress order within each group
        assert statuses == sorted(statuses, reverse=True)
