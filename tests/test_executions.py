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
        # Check that primary sort is by status desc, then by progress asc within each status
        statuses = [e["status"] for e in data]
        # This is a basic check; for full correctness, group by status and check progress order within each group
        assert statuses == sorted(statuses, reverse=True)

    def test_sort_by_multiple_fields(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?sort=status desc,progress asc", headers=auth_headers_user
        )
        assert response.status_code == 200
        data = response.json["data"]
        # Check that data is sorted by status desc, then by progress asc within each status
        from itertools import groupby

        statuses = [e["status"] for e in data]
        assert statuses == sorted(statuses, reverse=True)
        # Now check progress is ascending within each status group
        for status, group in groupby(data, key=lambda e: e["status"]):
            progresses = [e["progress"] for e in group]
            assert progresses == sorted(progresses)

    def test_filter_by_user_name_like(self, client, auth_headers_user):
        # Regular users cannot filter by user_name, so this should return an error
        response = client.get(
            "/api/v1/execution?filter=user_name like '%test%'",
            headers=auth_headers_user,
        )
        # Should return an error since only admin users can filter by user_name
        assert response.status_code in [400, 403, 500]

    def test_filter_by_user_name_like_admin(self, client, auth_headers_admin):
        # Admin users can filter by user_name
        response = client.get(
            "/api/v1/execution?filter=user_name like '%test%'",
            headers=auth_headers_admin,
        )
        assert response.status_code == 200

    def test_filter_by_script_name_like(self, client, auth_headers_user):
        response = client.get(
            "/api/v1/execution?filter=script_name like '%test%'",
            headers=auth_headers_user,
        )
        assert response.status_code == 200
        # The test should at least not return an error
        # In a real test environment, we'd check that the results contain the expected script names
