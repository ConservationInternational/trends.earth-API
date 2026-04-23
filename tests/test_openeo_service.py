"""Tests for the openEO dispatch service and monitoring task.

Covers:
- openeo_service: _resolve_backend_url, _connect_openeo, _build_process_graph,
  openeo_run Celery task (happy path, failure path)
- openeo_monitoring: monitor_openeo_jobs task, _poll_execution helper
  (all openEO terminal states, still-running transitions, skip logic)
"""

import datetime
import json
import os
from unittest.mock import MagicMock, Mock, patch, call
import uuid

import pytest

from gefapi import db
from gefapi.models import Execution, ExecutionLog, Script, User
from gefapi.services import openeo_service
from gefapi.services.openeo_service import _resolve_backend_url, _connect_openeo
from gefapi.tasks import openeo_monitoring
from gefapi.tasks.openeo_monitoring import _poll_execution

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-encryption")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_email():
    return f"openeo-svc-{uuid.uuid4().hex[:8]}@example.com"


def _make_user(app):
    with app.app_context():
        user = User(
            email=_unique_email(),
            password="password123",
            name="OEO User",
            country="TC",
            institution="Inst",
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)
        return user


def _make_script(app, user, compute_type="openeo", slug=None):
    slug = slug or f"soc-{uuid.uuid4().hex[:6]}"
    with app.app_context():
        user = db.session.merge(user)
        script = Script(
            name="Test Script",
            slug=slug,
            user_id=user.id,
        )
        script.compute_type = compute_type
        script.status = "SUCCESS"
        db.session.add(script)
        db.session.commit()
        db.session.refresh(script)
        return script


def _make_execution(app, user, script, status="READY", results=None):
    with app.app_context():
        user = db.session.merge(user)
        script = db.session.merge(script)
        execution = Execution(
            script_id=script.id,
            user_id=user.id,
            params={"year_initial": 2010, "year_final": 2020},
        )
        execution.status = status
        if results:
            execution.results = results
        db.session.add(execution)
        db.session.commit()
        db.session.refresh(execution)
        return execution


# ===========================================================================
# openeo_service._resolve_backend_url
# ===========================================================================


class TestResolveBackendURL:
    def test_returns_url_from_environment(self):
        env = {"OPENEO_BACKEND_URL": "https://env.openeo.example.com"}
        url = _resolve_backend_url(env)
        assert url == "https://env.openeo.example.com"

    def test_settings_fallback_when_env_missing(self):
        with patch(
            "gefapi.services.openeo_service.SETTINGS",
            {"OPENEO_DEFAULT_BACKEND_URL": "https://default.openeo.example.com"},
        ):
            url = _resolve_backend_url({})
        assert url == "https://default.openeo.example.com"

    def test_raises_when_nothing_configured(self):
        with patch("gefapi.services.openeo_service.SETTINGS", {}):
            with pytest.raises(ValueError, match="No openEO backend URL"):
                _resolve_backend_url({})

    def test_rejects_http_url(self):
        env = {"OPENEO_BACKEND_URL": "http://insecure.openeo.example.com"}
        with pytest.raises(ValueError, match="https://"):
            _resolve_backend_url(env)

    def test_rejects_url_without_host(self):
        env = {"OPENEO_BACKEND_URL": "https://"}
        with pytest.raises(ValueError):
            _resolve_backend_url(env)

    def test_environment_takes_priority_over_settings(self):
        env = {"OPENEO_BACKEND_URL": "https://env-wins.openeo.example.com"}
        with patch(
            "gefapi.services.openeo_service.SETTINGS",
            {"OPENEO_DEFAULT_BACKEND_URL": "https://settings.openeo.example.com"},
        ):
            url = _resolve_backend_url(env)
        assert url == "https://env-wins.openeo.example.com"


# ===========================================================================
# openeo_service._connect_openeo
# ===========================================================================


class TestConnectOpenEO:
    @patch("gefapi.services.openeo_service.openeo")
    def test_basic_auth_credentials_applied(self, mock_openeo):
        conn = MagicMock()
        mock_openeo.connect.return_value = conn
        creds_json = json.dumps({"type": "basic", "username": "u", "password": "p"})
        env = {"OPENEO_CREDENTIALS": creds_json}
        _connect_openeo("https://openeo.example.com", env)
        conn.authenticate_basic.assert_called_once_with(username="u", password="p")

    @patch("gefapi.services.openeo_service.openeo")
    def test_oidc_refresh_token_credentials_applied(self, mock_openeo):
        conn = MagicMock()
        mock_openeo.connect.return_value = conn
        creds = {
            "type": "oidc_refresh_token",
            "provider_id": "egi",
            "client_id": "trends-earth",
            "client_secret": "sec",
            "refresh_token": "rt",
        }
        env = {"OPENEO_CREDENTIALS": json.dumps(creds)}
        _connect_openeo("https://openeo.example.com", env)
        conn.authenticate_oidc_refresh_token.assert_called_once()
        kw = conn.authenticate_oidc_refresh_token.call_args.kwargs
        assert kw["refresh_token"] == "rt"
        assert kw["client_id"] == "trends-earth"

    @patch("gefapi.services.openeo_service.openeo")
    def test_oidc_alias_also_accepted(self, mock_openeo):
        """The shorter 'oidc' type alias should be treated the same as 'oidc_refresh_token'."""
        conn = MagicMock()
        mock_openeo.connect.return_value = conn
        creds = {
            "type": "oidc",
            "client_id": "c",
            "client_secret": "s",
            "refresh_token": "rt",
        }
        env = {"OPENEO_CREDENTIALS": json.dumps(creds)}
        _connect_openeo("https://openeo.example.com", env)
        conn.authenticate_oidc_refresh_token.assert_called_once()

    @patch("gefapi.services.openeo_service.openeo")
    def test_anonymous_when_no_credentials_provided(self, mock_openeo):
        conn = MagicMock()
        mock_openeo.connect.return_value = conn
        _connect_openeo("https://openeo.example.com", {})
        conn.authenticate_basic.assert_not_called()
        conn.authenticate_oidc_refresh_token.assert_not_called()

    @patch("gefapi.services.openeo_service.openeo")
    def test_unknown_credential_type_falls_through(self, mock_openeo):
        """Unknown credential type should not raise – connect anonymously."""
        conn = MagicMock()
        mock_openeo.connect.return_value = conn
        creds = {"type": "kerberos", "ticket": "TGT"}
        env = {"OPENEO_CREDENTIALS": json.dumps(creds)}
        result = _connect_openeo("https://openeo.example.com", env)
        conn.authenticate_basic.assert_not_called()

    @patch("gefapi.services.openeo_service.openeo")
    def test_invalid_json_credentials_raises_value_error(self, mock_openeo):
        conn = MagicMock()
        mock_openeo.connect.return_value = conn
        env = {"OPENEO_CREDENTIALS": "not-valid-json{{"}
        with pytest.raises(ValueError, match="OPENEO_CREDENTIALS JSON"):
            _connect_openeo("https://openeo.example.com", env)

    def test_raises_import_error_when_openeo_missing(self):
        with patch.object(openeo_service, "openeo", None):
            with pytest.raises(ImportError, match="openeo"):
                _connect_openeo("https://openeo.example.com", {})


# ===========================================================================
# openeo_service._build_process_graph
# ===========================================================================


class TestBuildProcessGraph:
    @patch("gefapi.services.openeo_service.openeo")
    def test_unknown_slug_raises_not_implemented(self, mock_openeo):
        conn = MagicMock()
        with pytest.raises(NotImplementedError, match="(?i)no openEO process graph"):
            openeo_service._build_process_graph(conn, "unknown-slug", {}, {})

    @patch("gefapi.services.openeo_service._build_soc_process_graph")
    def test_soc_slug_dispatches_to_builder(self, mock_soc_builder):
        mock_job = MagicMock()
        mock_soc_builder.return_value = mock_job
        conn = MagicMock()
        result = openeo_service._build_process_graph(
            conn, "soil-organic-carbon", {}, {"year_initial": 2010}
        )
        mock_soc_builder.assert_called_once_with(conn, {}, {"year_initial": 2010})
        assert result is mock_job


# ===========================================================================
# openeo_service.openeo_run Celery task
# ===========================================================================


class TestOpenEORunTask:
    """Tests for the openeo_run Celery task."""

    @patch("gefapi.services.openeo_service._build_process_graph")
    @patch("gefapi.services.openeo_service._connect_openeo")
    @patch("gefapi.services.openeo_service._resolve_backend_url")
    def test_happy_path_sets_ready_and_stores_job_id(
        self, mock_resolve, mock_connect, mock_build, app
    ):
        user = _make_user(app)
        script = _make_script(app, user)
        execution = _make_execution(app, user, script, status="PENDING")

        backend_url = "https://openeo.example.com"
        mock_resolve.return_value = backend_url

        conn = MagicMock()
        mock_connect.return_value = conn

        mock_job = MagicMock()
        mock_job.job_id = "oeo-job-abc123"
        mock_build.return_value = mock_job

        with app.app_context():
            openeo_service.openeo_run(
                execution.id,
                script.slug,
                {"OPENEO_BACKEND_URL": backend_url},
                {"year_initial": 2010, "year_final": 2020},
            )

            updated = Execution.query.get(execution.id)
            assert updated.status == "READY"
            assert updated.results["openeo_job_id"] == "oeo-job-abc123"
            assert updated.results["openeo_backend_url"] == backend_url

    @patch("gefapi.services.openeo_service._resolve_backend_url")
    def test_failure_sets_failed_status(self, mock_resolve, app):
        user = _make_user(app)
        script = _make_script(app, user)
        execution = _make_execution(app, user, script, status="PENDING")

        mock_resolve.side_effect = ValueError("No backend URL configured")

        with app.app_context():
            openeo_service.openeo_run(
                execution.id,
                script.slug,
                {},
                {},
            )

            updated = Execution.query.get(execution.id)
            assert updated.status == "FAILED"
            assert "openeo_error" in updated.results

    @patch("gefapi.services.openeo_service._build_process_graph")
    @patch("gefapi.services.openeo_service._connect_openeo")
    @patch("gefapi.services.openeo_service._resolve_backend_url")
    def test_happy_path_adds_log_entry(
        self, mock_resolve, mock_connect, mock_build, app
    ):
        user = _make_user(app)
        script = _make_script(app, user)
        execution = _make_execution(app, user, script, status="PENDING")

        mock_resolve.return_value = "https://openeo.example.com"
        conn = MagicMock()
        mock_connect.return_value = conn
        mock_job = MagicMock()
        mock_job.job_id = "oeo-job-xyz"
        mock_build.return_value = mock_job

        with app.app_context():
            openeo_service.openeo_run(execution.id, script.slug, {}, {})

            logs = ExecutionLog.query.filter_by(execution_id=execution.id).all()
            assert any("oeo-job-xyz" in lg.text for lg in logs)

    def test_gracefully_handles_missing_execution(self, app):
        """If the execution doesn't exist, openeo_run should not raise."""
        nonexistent_id = uuid.uuid4()
        with app.app_context():
            # Should not raise
            openeo_service.openeo_run(nonexistent_id, "soil-organic-carbon", {}, {})


# ===========================================================================
# openeo_monitoring._poll_execution
# ===========================================================================


class TestPollExecution:
    """Unit tests for _poll_execution()."""

    def _make_exec(self, app, status="READY", results=None):
        user = _make_user(app)
        script = _make_script(app, user)
        return _make_execution(app, user, script, status=status, results=results)

    def test_skips_when_no_job_id(self, app):
        with app.app_context():
            execution = self._make_exec(app, results={})
            execution = db.session.merge(execution)
            result = _poll_execution(execution)
        assert result is None

    def test_skips_when_no_backend_url(self, app):
        with app.app_context():
            execution = self._make_exec(
                app, results={"openeo_job_id": "job-123"}
            )
            execution = db.session.merge(execution)
            result = _poll_execution(execution)
        assert result is None

    @patch("gefapi.tasks.openeo_monitoring.openeo")
    def test_finished_job_sets_finished_status(self, mock_openeo, app):
        with app.app_context():
            execution = self._make_exec(
                app,
                status="RUNNING",
                results={
                    "openeo_job_id": "job-done",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )
            execution = db.session.merge(execution)

            conn = MagicMock()
            mock_openeo.connect.return_value = conn

            job = MagicMock()
            job.status.return_value = "finished"
            job.get_results.return_value = MagicMock(get_assets=MagicMock(return_value={}))
            conn.job.return_value = job

            result = _poll_execution(execution)

        assert result == "FINISHED"
        assert execution.status == "FINISHED"
        assert execution.end_date is not None

    @patch("gefapi.tasks.openeo_monitoring.openeo")
    def test_error_job_sets_failed_status(self, mock_openeo, app):
        with app.app_context():
            execution = self._make_exec(
                app,
                status="RUNNING",
                results={
                    "openeo_job_id": "job-err",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )
            execution = db.session.merge(execution)

            conn = MagicMock()
            mock_openeo.connect.return_value = conn

            job = MagicMock()
            job.status.return_value = "error"
            job.logs.return_value = [
                {"level": "error", "message": "out of memory"}
            ]
            conn.job.return_value = job

            result = _poll_execution(execution)

        assert result == "FAILED"
        assert execution.status == "FAILED"
        assert "out of memory" in execution.results.get("openeo_error", "")

    @patch("gefapi.tasks.openeo_monitoring.openeo")
    def test_canceled_job_sets_cancelled_status(self, mock_openeo, app):
        with app.app_context():
            execution = self._make_exec(
                app,
                status="RUNNING",
                results={
                    "openeo_job_id": "job-cancel",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )
            execution = db.session.merge(execution)

            conn = MagicMock()
            mock_openeo.connect.return_value = conn

            job = MagicMock()
            job.status.return_value = "canceled"
            conn.job.return_value = job

            result = _poll_execution(execution)

        assert result == "CANCELLED"
        assert execution.status == "CANCELLED"

    @patch("gefapi.tasks.openeo_monitoring.openeo")
    def test_running_job_updates_status_but_returns_none(self, mock_openeo, app):
        with app.app_context():
            execution = self._make_exec(
                app,
                status="READY",
                results={
                    "openeo_job_id": "job-running",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )
            execution = db.session.merge(execution)

            conn = MagicMock()
            mock_openeo.connect.return_value = conn

            job = MagicMock()
            job.status.return_value = "running"
            conn.job.return_value = job

            result = _poll_execution(execution)

        assert result is None
        assert execution.status == "RUNNING"

    @patch("gefapi.tasks.openeo_monitoring.openeo")
    def test_queued_job_does_not_change_terminal_flag(self, mock_openeo, app):
        """A queued job is non-terminal and should not change the status to terminal."""
        with app.app_context():
            execution = self._make_exec(
                app,
                status="READY",
                results={
                    "openeo_job_id": "job-q",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )
            execution = db.session.merge(execution)

            conn = MagicMock()
            mock_openeo.connect.return_value = conn

            job = MagicMock()
            job.status.return_value = "queued"
            conn.job.return_value = job

            result = _poll_execution(execution)

        assert result is None
        # Status should remain READY (not changed to RUNNING for queued)
        assert execution.status == "READY"

    @patch("gefapi.tasks.openeo_monitoring.openeo")
    def test_user_oidc_credentials_used_when_available(self, mock_openeo, app):
        """_poll_execution should authenticate with stored user credentials."""
        with app.app_context():
            user = _make_user(app)
            script = _make_script(app, user)

            user = db.session.merge(user)
            user.set_openeo_credentials(
                {
                    "type": "oidc_refresh_token",
                    "client_id": "c",
                    "client_secret": "s",
                    "refresh_token": "rt-poll",
                    "provider_id": "egi",
                }
            )
            db.session.commit()

            execution = _make_execution(
                app,
                user,
                script,
                status="RUNNING",
                results={
                    "openeo_job_id": "job-auth",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )

            with app.app_context():
                execution = db.session.merge(execution)
                conn = MagicMock()
                mock_openeo.connect.return_value = conn

                job = MagicMock()
                job.status.return_value = "finished"
                job.get_results.return_value = MagicMock(get_assets=MagicMock(return_value={}))
                conn.job.return_value = job

                _poll_execution(execution)

            conn.authenticate_oidc_refresh_token.assert_called_once()
            kw = conn.authenticate_oidc_refresh_token.call_args.kwargs
            assert kw["refresh_token"] == "rt-poll"

    @patch("gefapi.tasks.openeo_monitoring.openeo")
    def test_poll_backend_error_returns_none(self, mock_openeo, app):
        """Network / backend errors during polling should be swallowed (returns None)."""
        with app.app_context():
            execution = self._make_exec(
                app,
                status="RUNNING",
                results={
                    "openeo_job_id": "job-net-err",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )
            execution = db.session.merge(execution)

            conn = MagicMock()
            mock_openeo.connect.return_value = conn
            conn.job.side_effect = Exception("connection refused")

            result = _poll_execution(execution)

        assert result is None

    def test_returns_none_when_openeo_import_missing(self, app):
        with app.app_context():
            execution = self._make_exec(
                app,
                results={
                    "openeo_job_id": "job-no-pkg",
                    "openeo_backend_url": "https://openeo.example.com",
                },
            )
            execution = db.session.merge(execution)

            with patch.object(openeo_monitoring, "openeo", None):
                result = _poll_execution(execution)

        assert result is None


# ===========================================================================
# openeo_monitoring.monitor_openeo_jobs Celery task
# ===========================================================================


class TestMonitorOpenEOJobs:
    @patch("gefapi.tasks.openeo_monitoring._poll_execution")
    def test_returns_zero_counts_when_no_active_executions(self, mock_poll, app):
        with app.app_context():
            result = openeo_monitoring.monitor_openeo_jobs()
        assert result["checked"] == 0
        assert result["finished"] == 0
        mock_poll.assert_not_called()

    @patch("gefapi.tasks.openeo_monitoring._poll_execution")
    def test_counts_finished_executions(self, mock_poll, app):
        user = _make_user(app)
        script = _make_script(app, user, compute_type="openeo")
        execution = _make_execution(
            app,
            user,
            script,
            status="RUNNING",
            results={
                "openeo_job_id": "j1",
                "openeo_backend_url": "https://openeo.example.com",
            },
        )

        mock_poll.return_value = "FINISHED"

        with app.app_context():
            result = openeo_monitoring.monitor_openeo_jobs()

        assert result["finished"] >= 1

    @patch("gefapi.tasks.openeo_monitoring._poll_execution")
    def test_counts_failed_executions(self, mock_poll, app):
        user = _make_user(app)
        script = _make_script(app, user, compute_type="openeo")
        _make_execution(
            app,
            user,
            script,
            status="READY",
            results={
                "openeo_job_id": "j-fail",
                "openeo_backend_url": "https://openeo.example.com",
            },
        )

        mock_poll.return_value = "FAILED"

        with app.app_context():
            result = openeo_monitoring.monitor_openeo_jobs()

        assert result["failed"] >= 1

    @patch("gefapi.tasks.openeo_monitoring._poll_execution")
    def test_skips_non_openeo_scripts(self, mock_poll, app):
        """Executions for gee-type scripts must not be polled."""
        user = _make_user(app)
        gee_script = _make_script(app, user, compute_type="gee")
        _make_execution(
            app,
            user,
            gee_script,
            status="RUNNING",
            results={"openeo_job_id": "j-gee"},
        )

        with app.app_context():
            result = openeo_monitoring.monitor_openeo_jobs()

        mock_poll.assert_not_called()

    @patch("gefapi.tasks.openeo_monitoring._poll_execution")
    def test_poll_exception_does_not_abort_task(self, mock_poll, app):
        """An exception in _poll_execution should not abort the whole task."""
        user = _make_user(app)
        script = _make_script(app, user, compute_type="openeo")
        _make_execution(
            app,
            user,
            script,
            status="RUNNING",
            results={
                "openeo_job_id": "j-exc",
                "openeo_backend_url": "https://openeo.example.com",
            },
        )

        mock_poll.side_effect = RuntimeError("unexpected")

        with app.app_context():
            # Should not raise
            result = openeo_monitoring.monitor_openeo_jobs()

        # Task completes and returns counts (failed poll = 0 counted)
        assert "checked" in result
