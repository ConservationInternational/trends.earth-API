"""
Tests for the /status/swarm monitoring endpoint.

Covers:
- GET /status/swarm
"""

from unittest.mock import patch

import pytest


@pytest.mark.usefixtures("client")
class TestSwarmStatusEndpoint:
    """Tests for GET /api/v1/status/swarm"""

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/status/swarm")
        assert resp.status_code == 401

    def test_forbidden_for_regular_user(self, client, auth_headers_user):
        resp = client.get("/api/v1/status/swarm", headers=auth_headers_user)
        assert resp.status_code == 403

    @patch("gefapi.tasks.status_monitoring.get_cached_swarm_status")
    def test_returns_swarm_status_for_admin(
        self, mock_swarm, client, auth_headers_admin
    ):
        mock_swarm.return_value = {
            "swarm_active": True,
            "total_nodes": 2,
            "total_managers": 1,
            "total_workers": 1,
            "error": None,
            "nodes": [
                {
                    "id": "node-1",
                    "hostname": "manager-01",
                    "role": "manager",
                    "is_manager": True,
                    "state": "ready",
                    "availability": "active",
                }
            ],
            "cache_info": {
                "cached_at": "2025-01-15T10:30:00Z",
                "cache_ttl": 300,
                "cache_key": "docker_swarm_status",
                "source": "cached",
            },
        }

        resp = client.get("/api/v1/status/swarm", headers=auth_headers_admin)
        assert resp.status_code == 200
        data = resp.json
        assert "data" in data
        assert data["data"]["swarm_active"] is True
        assert data["data"]["total_nodes"] == 2

    @patch("gefapi.tasks.status_monitoring.get_cached_swarm_status")
    def test_returns_inactive_swarm(self, mock_swarm, client, auth_headers_admin):
        mock_swarm.return_value = {
            "swarm_active": False,
            "error": "Not in swarm mode",
            "nodes": [],
            "total_nodes": 0,
            "total_managers": 0,
            "total_workers": 0,
            "cache_info": {
                "cached_at": "2025-01-15T10:30:00Z",
                "cache_ttl": 0,
                "cache_key": "docker_swarm_status",
                "source": "real_time_fallback",
            },
        }

        resp = client.get("/api/v1/status/swarm", headers=auth_headers_admin)
        assert resp.status_code == 200
        data = resp.json
        assert data["data"]["swarm_active"] is False
        assert data["data"]["error"] == "Not in swarm mode"

    @patch("gefapi.tasks.status_monitoring.get_cached_swarm_status")
    def test_handles_cache_failure(self, mock_swarm, client, auth_headers_admin):
        mock_swarm.side_effect = Exception("Redis unavailable")

        resp = client.get("/api/v1/status/swarm", headers=auth_headers_admin)
        # Endpoint catches this and returns a fallback
        assert resp.status_code == 200
        data = resp.json
        assert data["data"]["swarm_active"] is False


@pytest.mark.usefixtures("client")
class TestClusterStatusEndpoint:
    """Tests for GET /api/v1/status/cluster (alias for /status/swarm)"""

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/status/cluster")
        assert resp.status_code == 401

    def test_forbidden_for_regular_user(self, client, auth_headers_user):
        resp = client.get("/api/v1/status/cluster", headers=auth_headers_user)
        assert resp.status_code == 403

    @patch("gefapi.tasks.status_monitoring.get_cached_swarm_status")
    def test_returns_cluster_status_for_admin(
        self, mock_swarm, client, auth_headers_admin
    ):
        mock_swarm.return_value = {
            "swarm_active": True,
            "total_nodes": 1,
            "total_managers": 1,
            "total_workers": 0,
            "error": None,
            "nodes": [],
            "cache_info": {
                "cached_at": "2025-01-15T10:30:00Z",
                "cache_ttl": 300,
                "cache_key": "docker_swarm_status",
                "source": "cached",
            },
        }

        resp = client.get("/api/v1/status/cluster", headers=auth_headers_admin)
        assert resp.status_code == 200
        data = resp.json
        assert "data" in data
        assert data["data"]["swarm_active"] is True
