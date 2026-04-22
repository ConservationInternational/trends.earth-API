"""Tests for execution cancellation REST endpoints"""

from unittest.mock import Mock, patch
import uuid

import pytest

from gefapi.models.execution import Execution
from gefapi.models.execution_log import ExecutionLog


@pytest.mark.usefixtures("app", "db_session")
class TestExecutionCancellationEndpoints:
    """Test execution cancellation REST API endpoints"""

    def test_cancel_execution_success_own_execution(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test successful cancellation of user's own execution"""
        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch.object(mock_celery, "send_task") as mock_send_task,
        ):
            # Mock successful Celery task result
            mock_task_result = Mock()
            mock_task_result.get.return_value = {
                "docker_service_stopped": True,
                "docker_container_stopped": False,
                "errors": [],
            }
            mock_send_task.return_value = mock_task_result

            # Re-query objects to attach to current session
            regular_user = db_session.merge(regular_user)
            sample_script = db_session.merge(sample_script)

            # Create a RUNNING execution for the user
            execution = Execution(
                script_id=sample_script.id,
                params={"test": "param"},
                user_id=regular_user.id,
            )
            execution.status = "RUNNING"
            db_session.add(execution)
            db_session.commit()

            # Mock GEE service
            with patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee:
                mock_gee.return_value = [
                    {
                        "task_id": "TEST_GEE_TASK_ID",
                        "success": True,
                        "status": "CANCELLED",
                        "error": None,
                    }
                ]

                # Make the cancellation request
                response = client.post(
                    f"/api/v1/execution/{execution.id}/cancel",
                    headers=auth_headers_user,
                )

                # Verify response
                assert response.status_code == 202

                response_data = response.get_json()
                assert "data" in response_data
                assert "execution" in response_data["data"]
                assert "cancellation_details" in response_data["data"]

                # Verify execution status
                execution_data = response_data["data"]["execution"]
                assert execution_data["status"] == "CANCELLING"

                # Verify cancellation details
                details = response_data["data"]["cancellation_details"]
                assert details["execution_id"] == str(execution.id)
                assert details["previous_status"] == "RUNNING"
                assert details["queued"] is True
                assert details["new_status"] == "CANCELLING"
                assert details["task_id"] is not None

                # Verify Celery task was called correctly
                mock_send_task.assert_called_once_with(
                    "gefapi.tasks.execution_cancellation.cancel_execution_workflow",
                    args=[str(execution.id)],
                    queue="build",
                )

    def test_cancel_execution_admin_cancel_other_user(
        self, client, auth_headers_admin, regular_user, sample_script, db_session
    ):
        """Test admin can cancel another user's execution"""
        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch.object(mock_celery, "send_task") as mock_send_task,
        ):
            # Mock successful Celery task result
            mock_task_result = Mock()
            mock_task_result.get.return_value = {
                "docker_service_stopped": False,
                "docker_container_stopped": True,
                "errors": [],
            }
            mock_send_task.return_value = mock_task_result

            # Re-query objects to attach to current session
            regular_user = db_session.merge(regular_user)
            sample_script = db_session.merge(sample_script)

            # Create a RUNNING execution for regular user
            execution = Execution(
                script_id=sample_script.id,
                params={"test": "param"},
                user_id=regular_user.id,
            )
            execution.status = "RUNNING"
            db_session.add(execution)
            db_session.commit()

            # Mock GEE service (no GEE tasks found)
            with patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee:
                mock_gee.return_value = []

                # Admin makes the cancellation request
                response = client.post(
                    f"/api/v1/execution/{execution.id}/cancel",
                    headers=auth_headers_admin,
                )

                # Verify response
                assert response.status_code == 202

                response_data = response.get_json()
                assert response_data["data"]["execution"]["status"] == "CANCELLING"

                # Verify cancellation details
                details = response_data["data"]["cancellation_details"]
                assert details["queued"] is True
                assert details["new_status"] == "CANCELLING"

    def test_cancel_execution_forbidden_other_user(
        self, client, auth_headers_user, admin_user, sample_script, db_session
    ):
        """Test regular user cannot cancel another user's execution"""
        # Re-query admin_user to attach to current session
        admin_user = db_session.merge(admin_user)
        sample_script = db_session.merge(sample_script)

        # Create a RUNNING execution for admin user
        execution = Execution(
            script_id=sample_script.id, params={"test": "param"}, user_id=admin_user.id
        )
        execution.status = "RUNNING"
        db_session.add(execution)
        db_session.commit()

        # Regular user tries to cancel admin's execution
        response = client.post(
            f"/api/v1/execution/{execution.id}/cancel", headers=auth_headers_user
        )

        # Verify forbidden response - could be 404 if execution is not found due to user filtering
        assert response.status_code in [
            403,
            404,
        ]  # Both are valid - depends on implementation
        response_data = response.get_json()
        assert "detail" in response_data
        # Either "forbidden" or "not found" message is acceptable
        assert any(
            keyword in response_data["detail"].lower()
            for keyword in ["forbidden", "not found", "only cancel your own"]
        )

    def test_cancel_execution_not_found(self, client, auth_headers_user):
        """Test cancelling non-existent execution returns 404"""
        non_existent_id = str(uuid.uuid4())

        response = client.post(
            f"/api/v1/execution/{non_existent_id}/cancel", headers=auth_headers_user
        )

        assert response.status_code == 404
        response_data = response.get_json()
        assert "detail" in response_data
        assert "not found" in response_data["detail"].lower()

    def test_cancel_execution_already_finished(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test cannot cancel already finished execution"""
        # Re-query objects to attach to current session
        regular_user = db_session.merge(regular_user)
        sample_script = db_session.merge(sample_script)

        # Create a FINISHED execution
        execution = Execution(
            script_id=sample_script.id,
            params={"test": "param"},
            user_id=regular_user.id,
        )
        execution.status = "FINISHED"
        db_session.add(execution)
        db_session.commit()

        response = client.post(
            f"/api/v1/execution/{execution.id}/cancel", headers=auth_headers_user
        )

        assert response.status_code == 400
        response_data = response.get_json()
        assert "detail" in response_data
        assert "Cannot cancel execution in FINISHED state" in response_data["detail"]

    def test_cancel_execution_already_cancelled(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test cannot cancel already cancelled execution"""
        # Re-query objects to attach to current session
        regular_user = db_session.merge(regular_user)
        sample_script = db_session.merge(sample_script)

        # Create a CANCELLED execution
        execution = Execution(
            script_id=sample_script.id,
            params={"test": "param"},
            user_id=regular_user.id,
        )
        execution.status = "CANCELLED"
        db_session.add(execution)
        db_session.commit()

        response = client.post(
            f"/api/v1/execution/{execution.id}/cancel", headers=auth_headers_user
        )

        assert response.status_code == 400
        response_data = response.get_json()
        assert "detail" in response_data
        assert "Cannot cancel execution in CANCELLED state" in response_data["detail"]

    def test_cancel_execution_failed_status(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test cannot cancel failed execution"""
        # Re-query objects to attach to current session
        regular_user = db_session.merge(regular_user)
        sample_script = db_session.merge(sample_script)

        # Create a FAILED execution
        execution = Execution(
            script_id=sample_script.id,
            params={"test": "param"},
            user_id=regular_user.id,
        )
        execution.status = "FAILED"
        db_session.add(execution)
        db_session.commit()

        response = client.post(
            f"/api/v1/execution/{execution.id}/cancel", headers=auth_headers_user
        )

        assert response.status_code == 400
        response_data = response.get_json()
        assert "detail" in response_data
        assert "Cannot cancel execution in FAILED state" in response_data["detail"]

    def test_cancel_execution_unauthorized(
        self, client, regular_user, sample_script, db_session
    ):
        """Test cancellation requires authentication"""
        # Re-query objects to attach to current session
        regular_user = db_session.merge(regular_user)
        sample_script = db_session.merge(sample_script)

        # Create an execution
        execution = Execution(
            script_id=sample_script.id,
            params={"test": "param"},
            user_id=regular_user.id,
        )
        execution.status = "RUNNING"
        db_session.add(execution)
        db_session.commit()

        # Make request without authentication
        response = client.post(f"/api/v1/execution/{execution.id}/cancel")

        assert response.status_code in [
            401,
            422,
        ]  # JWT required error - could be either

    def test_cancel_execution_docker_task_timeout(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test cancellation handles Docker task timeout gracefully"""

        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch.object(mock_celery, "send_task") as mock_send_task,
        ):
            # Mock enqueue failure
            mock_send_task.side_effect = Exception("Worker lost")

            # Re-query objects to attach to current session
            regular_user = db_session.merge(regular_user)
            sample_script = db_session.merge(sample_script)

            # Create a RUNNING execution
            execution = Execution(
                script_id=sample_script.id,
                params={"test": "param"},
                user_id=regular_user.id,
            )
            execution.status = "RUNNING"
            db_session.add(execution)
            db_session.commit()

            # Mock GEE service
            with patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee:
                mock_gee.return_value = []

                response = client.post(
                    f"/api/v1/execution/{execution.id}/cancel",
                    headers=auth_headers_user,
                )

                # Enqueue failure currently bubbles up as a server error
                assert response.status_code == 500

                response_data = response.get_json()
                assert "detail" in response_data
                assert response_data["detail"] == "Failed to cancel execution"

    def test_cancel_execution_with_gee_tasks(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test cancellation with GEE tasks in execution logs"""
        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch.object(mock_celery, "send_task") as mock_send_task,
        ):
            # Mock successful Docker cancellation
            mock_task_result = Mock()
            mock_task_result.get.return_value = {
                "docker_service_stopped": True,
                "docker_container_stopped": False,
                "errors": [],
            }
            mock_send_task.return_value = mock_task_result

            # Re-query objects to attach to current session
            regular_user = db_session.merge(regular_user)
            sample_script = db_session.merge(sample_script)

            # Create a RUNNING execution
            execution = Execution(
                script_id=sample_script.id,
                params={"test": "param"},
                user_id=regular_user.id,
            )
            execution.status = "RUNNING"
            db_session.add(execution)
            db_session.commit()

            # Add execution logs with GEE task IDs
            log1 = ExecutionLog(
                text="Starting GEE task ABCD1234EFGH5678IJKL9012",
                level="INFO",
                execution_id=execution.id,
            )
            log2 = ExecutionLog(
                text="GEE task XYZA9876BCDE5432FGHI1234 in progress",
                level="INFO",
                execution_id=execution.id,
            )
            db_session.add(log1)
            db_session.add(log2)
            db_session.commit()

            # Mock GEE cancellation with mixed results
            with patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee:
                mock_gee.return_value = [
                    {
                        "task_id": "ABCD1234EFGH5678IJKL9012",
                        "success": True,
                        "status": "CANCELLED",
                        "error": None,
                    },
                    {
                        "task_id": "XYZA9876BCDE5432FGHI1234",
                        "success": False,
                        "status": "UNKNOWN",
                        "error": "Task not found",
                    },
                ]

                response = client.post(
                    f"/api/v1/execution/{execution.id}/cancel",
                    headers=auth_headers_user,
                )

                assert response.status_code == 202

                response_data = response.get_json()
                details = response_data["data"]["cancellation_details"]

                # Verify async queue metadata
                assert details["queued"] is True
                assert details["new_status"] == "CANCELLING"
                assert details["task_id"] is not None

    def test_cancel_execution_service_error_recovery(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test cancellation handles service errors but still updates execution status"""
        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch.object(mock_celery, "send_task") as mock_send_task,
        ):
            # Mock Docker task with partial failure
            mock_task_result = Mock()
            mock_task_result.get.return_value = {
                "docker_service_stopped": False,
                "docker_container_stopped": False,
                "errors": [
                    "Docker service not found",
                    "Docker container not found",
                ],
            }
            mock_send_task.return_value = mock_task_result

            # Re-query objects to attach to current session
            regular_user = db_session.merge(regular_user)
            sample_script = db_session.merge(sample_script)

            # Create a RUNNING execution
            execution = Execution(
                script_id=sample_script.id,
                params={"test": "param"},
                user_id=regular_user.id,
            )
            execution.status = "RUNNING"
            db_session.add(execution)
            db_session.commit()

            # Mock GEE service failure
            with patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee:
                mock_gee.side_effect = Exception("GEE API error")

                response = client.post(
                    f"/api/v1/execution/{execution.id}/cancel",
                    headers=auth_headers_user,
                )

                # Should still return accepted response
                assert response.status_code == 202

                response_data = response.get_json()
                assert response_data["data"]["execution"]["status"] == "CANCELLING"

                # Queue metadata is returned immediately
                details = response_data["data"]["cancellation_details"]
                assert details["queued"] is True
                assert details["new_status"] == "CANCELLING"

    def test_cancel_execution_invalid_uuid(self, client, auth_headers_user):
        """Test cancellation with invalid execution ID format"""
        response = client.post(
            "/api/v1/execution/invalid-uuid/cancel", headers=auth_headers_user
        )

        # Should return error for invalid UUID
        assert response.status_code in [
            400,
            404,
            500,
        ]  # Depends on validation implementation

    def test_cancel_execution_logs_created(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test that cancellation creates appropriate execution logs"""
        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch.object(mock_celery, "send_task") as mock_send_task,
        ):
            # Mock successful cancellation
            mock_task_result = Mock()
            mock_task_result.get.return_value = {
                "docker_service_stopped": True,
                "docker_container_stopped": False,
                "errors": [],
            }
            mock_send_task.return_value = mock_task_result

            # Re-query objects to attach to current session
            regular_user = db_session.merge(regular_user)
            sample_script = db_session.merge(sample_script)

            # Create a RUNNING execution
            execution = Execution(
                script_id=sample_script.id,
                params={"test": "param"},
                user_id=regular_user.id,
            )
            execution.status = "RUNNING"
            db_session.add(execution)
            db_session.commit()

            initial_log_count = ExecutionLog.query.filter_by(
                execution_id=execution.id
            ).count()

            # Mock GEE service with successful cancellation
            with patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee:
                mock_gee.return_value = [
                    {
                        "task_id": "TEST_TASK_123",
                        "success": True,
                        "status": "CANCELLED",
                        "error": None,
                    }
                ]

                response = client.post(
                    f"/api/v1/execution/{execution.id}/cancel",
                    headers=auth_headers_user,
                )

                assert response.status_code == 202

                # Verify cancellation log was created
                logs = ExecutionLog.query.filter_by(execution_id=execution.id).all()
                assert len(logs) > initial_log_count

                # Find the cancellation log
                cancellation_log = next(
                    (
                        log
                        for log in logs
                        if "cancellation requested by user" in log.text.lower()
                    ),
                    None,
                )
                assert cancellation_log is not None
                assert cancellation_log.level == "INFO"


@pytest.mark.usefixtures("app", "db_session")
class TestBatchExecutionCancellationEndpoint:
    """Test cancellation of batch-type executions via REST endpoint"""

    def test_cancel_batch_execution_terminates_jobs(
        self, client, auth_headers_user, regular_user, batch_script, db_session
    ):
        """Test that cancelling a batch execution uses async cancellation workflow"""
        regular_user = db_session.merge(regular_user)
        batch_script = db_session.merge(batch_script)

        # Create a RUNNING batch execution with Batch job IDs in results
        execution = Execution(
            script_id=batch_script.id,
            params={"test": "param"},
            user_id=regular_user.id,
        )
        execution.status = "RUNNING"
        execution.results = {
            "batch_jobs": {"extract": "batch-job-111", "match": "batch-job-222"},
            "status": "SUBMITTED",
        }
        db_session.add(execution)
        db_session.commit()

        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee,
        ):
            mock_task_result = Mock()
            mock_task_result.id = "batch-cancel-task"
            mock_celery.send_task.return_value = mock_task_result
            mock_gee.return_value = []

            response = client.post(
                f"/api/v1/execution/{execution.id}/cancel",
                headers=auth_headers_user,
            )

            assert response.status_code == 202
            data = response.get_json()["data"]

            assert data["execution"]["status"] == "CANCELLING"
            details = data["cancellation_details"]
            assert details["queued"] is True
            assert details["new_status"] == "CANCELLING"
            assert details["task_id"] == "batch-cancel-task"


@pytest.mark.usefixtures("app", "db_session")
class TestExecutionCancellationIntegration:
    """Integration tests for execution cancellation with all components"""

    def test_full_cancellation_workflow(
        self, client, auth_headers_user, regular_user, sample_script, db_session
    ):
        """Test complete cancellation workflow from endpoint to database"""
        with (
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch.object(mock_celery, "send_task") as mock_send_task,
        ):
            # Mock comprehensive cancellation result
            mock_task_result = Mock()
            mock_task_result.get.return_value = {
                "docker_service_stopped": True,
                "docker_container_stopped": True,
                "errors": [],
            }
            mock_send_task.return_value = mock_task_result

            # Re-query objects to attach to current session
            regular_user = db_session.merge(regular_user)
            sample_script = db_session.merge(sample_script)

            # Create and verify initial execution state
            execution = Execution(
                script_id=sample_script.id,
                params={"region": "test", "year": 2023},
                user_id=regular_user.id,
            )
            execution.status = "RUNNING"
            execution.progress = 50
            db_session.add(execution)
            db_session.commit()

            initial_id = execution.id
            initial_start_date = execution.start_date

            # Add some logs to simulate running execution
            log1 = ExecutionLog(
                text="Execution started successfully",
                level="INFO",
                execution_id=execution.id,
            )
            log2 = ExecutionLog(
                text="Starting GEE task TEST123456789012345678901234",
                level="INFO",
                execution_id=execution.id,
            )
            db_session.add(log1)
            db_session.add(log2)
            db_session.commit()

            # Mock successful GEE cancellation
            with patch(
                "gefapi.services.gee_service.GEEService.cancel_gee_tasks_from_execution"
            ) as mock_gee:
                mock_gee.return_value = [
                    {
                        "task_id": "TEST123456789012345678901234",
                        "success": True,
                        "status": "CANCELLED",
                        "error": None,
                    }
                ]

                # Execute cancellation
                response = client.post(
                    f"/api/v1/execution/{execution.id}/cancel",
                    headers=auth_headers_user,
                )

                # Verify HTTP response
                assert response.status_code == 202
                response_data = response.get_json()

                # Verify response structure
                assert "data" in response_data
                assert "execution" in response_data["data"]
                assert "cancellation_details" in response_data["data"]

                # Verify execution state in response
                exec_data = response_data["data"]["execution"]
                assert exec_data["id"] == str(initial_id)
                assert exec_data["status"] == "CANCELLING"
                assert exec_data["start_date"] == initial_start_date.isoformat()
                assert exec_data["end_date"] is None

                # Verify cancellation details
                details = response_data["data"]["cancellation_details"]
                assert details["execution_id"] == str(initial_id)
                assert details["previous_status"] == "RUNNING"
                assert details["queued"] is True
                assert details["new_status"] == "CANCELLING"
                assert details["task_id"] is not None

                # Verify database state
                db_session.refresh(execution)
                assert execution.status == "CANCELLING"

                # Verify logs were created
                logs = ExecutionLog.query.filter_by(execution_id=execution.id).all()
                assert len(logs) >= 3  # original 2 + cancellation log

                cancellation_log = next(
                    (
                        log
                        for log in logs
                        if "cancellation requested by user" in log.text.lower()
                    ),
                    None,
                )
                assert cancellation_log is not None

                # Verify service calls
                mock_send_task.assert_called_once_with(
                    "gefapi.tasks.execution_cancellation.cancel_execution_workflow",
                    args=[str(execution.id)],
                    queue="build",
                )
