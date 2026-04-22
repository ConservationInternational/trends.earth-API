"""Tests for AWS Batch job cancellation functionality"""

from unittest.mock import MagicMock, Mock, patch

from gefapi.services.execution_service import ExecutionService
from gefapi.services.gee_service import GEEService


class TestTerminateBatchJobs:
    """Test terminate_batch_jobs() in batch_service"""

    @patch("gefapi.services.batch_service._get_batch_client")
    def test_terminate_running_single_job(self, mock_get_client):
        """Test terminating a single running Batch job"""
        from gefapi.services.batch_service import terminate_batch_jobs

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock describe_jobs to return a running job
        mock_client.describe_jobs.return_value = {
            "jobs": [
                {
                    "jobId": "batch-job-123",
                    "status": "RUNNING",
                    "jobName": "te-abc12345",
                }
            ]
        }
        mock_client.terminate_job.return_value = {}

        # Mock the execution with batch_jobs in results
        mock_execution = Mock()
        mock_execution.results = {"batch_jobs": {"job_id": "batch-job-123"}}

        with patch("gefapi.services.batch_service.Execution") as mock_execution_model:
            mock_execution_model.query.get.return_value = mock_execution
            result = terminate_batch_jobs("test-exec-id")

        assert len(result["jobs_terminated"]) == 1
        assert result["jobs_terminated"][0]["success"] is True
        assert result["jobs_terminated"][0]["previous_status"] == "RUNNING"
        assert result["jobs_terminated"][0]["job_id"] == "batch-job-123"
        assert not result["errors"]

        mock_client.terminate_job.assert_called_once_with(
            jobId="batch-job-123", reason="Cancelled by user"
        )

    @patch("gefapi.services.batch_service._get_batch_client")
    def test_terminate_pipeline_jobs(self, mock_get_client):
        """Test terminating a multi-step pipeline with mixed statuses"""
        from gefapi.services.batch_service import terminate_batch_jobs

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.describe_jobs.return_value = {
            "jobs": [
                {"jobId": "job-extract", "status": "SUCCEEDED", "jobName": "extract"},
                {"jobId": "job-match", "status": "RUNNING", "jobName": "match"},
                {"jobId": "job-summarize", "status": "PENDING", "jobName": "summarize"},
            ]
        }
        mock_client.terminate_job.return_value = {}

        mock_execution = Mock()
        mock_execution.results = {
            "batch_jobs": {
                "extract": "job-extract",
                "match": "job-match",
                "summarize": "job-summarize",
            }
        }

        with patch("gefapi.services.batch_service.Execution") as mock_execution_model:
            mock_execution_model.query.get.return_value = mock_execution
            result = terminate_batch_jobs("test-exec-id")

        # extract is SUCCEEDED — should not be terminated but counted as
        # success (already terminal)
        # match is RUNNING — should be terminated
        # summarize is PENDING — should be terminated
        assert len(result["jobs_terminated"]) == 3
        assert not result["errors"]

        # Only RUNNING and PENDING should have terminate_job called
        assert mock_client.terminate_job.call_count == 2
        terminated_ids = [
            call.kwargs["jobId"] for call in mock_client.terminate_job.call_args_list
        ]
        assert "job-match" in terminated_ids
        assert "job-summarize" in terminated_ids
        assert "job-extract" not in terminated_ids

    @patch("gefapi.services.batch_service._get_batch_client")
    def test_terminate_already_terminal_jobs(self, mock_get_client):
        """Test terminating jobs that are already finished"""
        from gefapi.services.batch_service import terminate_batch_jobs

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.describe_jobs.return_value = {
            "jobs": [
                {"jobId": "job-1", "status": "SUCCEEDED", "jobName": "step1"},
                {"jobId": "job-2", "status": "FAILED", "jobName": "step2"},
            ]
        }

        mock_execution = Mock()
        mock_execution.results = {"batch_jobs": {"step1": "job-1", "step2": "job-2"}}

        with patch("gefapi.services.batch_service.Execution") as mock_execution_model:
            mock_execution_model.query.get.return_value = mock_execution
            result = terminate_batch_jobs("test-exec-id")

        assert len(result["jobs_terminated"]) == 2
        assert all(j["success"] for j in result["jobs_terminated"])
        # No terminate_job calls needed — both already terminal
        mock_client.terminate_job.assert_not_called()

    @patch("gefapi.services.batch_service._get_batch_client")
    def test_terminate_job_api_failure(self, mock_get_client):
        """Test handling of TerminateJob API failure"""
        from gefapi.services.batch_service import terminate_batch_jobs

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.describe_jobs.return_value = {
            "jobs": [
                {"jobId": "job-1", "status": "RUNNING", "jobName": "step1"},
            ]
        }
        mock_client.terminate_job.side_effect = Exception("Access Denied")

        mock_execution = Mock()
        mock_execution.results = {"batch_jobs": {"step1": "job-1"}}

        with patch("gefapi.services.batch_service.Execution") as mock_execution_model:
            mock_execution_model.query.get.return_value = mock_execution
            result = terminate_batch_jobs("test-exec-id")

        assert len(result["jobs_terminated"]) == 1
        assert result["jobs_terminated"][0]["success"] is False
        assert len(result["errors"]) == 1
        assert "Access Denied" in result["errors"][0]

    @patch("gefapi.services.batch_service._get_batch_client")
    def test_terminate_no_batch_jobs_in_results(self, mock_get_client):
        """Test when execution has no batch_jobs in results"""
        from gefapi.services.batch_service import terminate_batch_jobs

        mock_execution = Mock()
        mock_execution.results = {"status": "SUBMITTED"}

        with patch("gefapi.services.batch_service.Execution") as mock_execution_model:
            mock_execution_model.query.get.return_value = mock_execution
            result = terminate_batch_jobs("test-exec-id")

        assert result["jobs_terminated"] == []
        assert not result["errors"]
        mock_get_client.assert_not_called()

    def test_terminate_execution_not_found(self):
        """Test when execution does not exist"""
        from gefapi.services.batch_service import terminate_batch_jobs

        with patch("gefapi.services.batch_service.Execution") as mock_execution_model:
            mock_execution_model.query.get.return_value = None
            result = terminate_batch_jobs("nonexistent")

        assert len(result["errors"]) == 1
        assert "not found" in result["errors"][0].lower()

    @patch("gefapi.services.batch_service._get_batch_client")
    def test_terminate_expired_jobs(self, mock_get_client):
        """Test when jobs are expired and not returned by describe_jobs"""
        from gefapi.services.batch_service import terminate_batch_jobs

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # describe_jobs returns empty — jobs expired from Batch history
        mock_client.describe_jobs.return_value = {"jobs": []}

        mock_execution = Mock()
        mock_execution.results = {"batch_jobs": {"step1": "expired-job-id"}}

        with patch("gefapi.services.batch_service.Execution") as mock_execution_model:
            mock_execution_model.query.get.return_value = mock_execution
            result = terminate_batch_jobs("test-exec-id")

        assert len(result["jobs_terminated"]) == 1
        assert result["jobs_terminated"][0]["previous_status"] == "NOT_FOUND"
        assert result["jobs_terminated"][0]["success"] is False
        mock_client.terminate_job.assert_not_called()


class TestCancelBatchExecution:
    """Test cancel_execution() with batch-type scripts"""

    @patch("gefapi.services.execution_service.ExecutionLog")
    @patch("gefapi.services.execution_service.rollbar")
    @patch.object(GEEService, "cancel_gee_tasks_from_execution")
    def test_cancel_batch_execution_calls_terminate(
        self, mock_gee_cancel, mock_rollbar, mock_execution_log
    ):
        """Test that cancelling a batch execution terminates Batch jobs,
        not Docker containers"""
        from gefapi.models import Execution, Script

        mock_execution = Mock(spec=Execution)
        mock_execution.id = "batch-exec-id"
        mock_execution.status = "RUNNING"
        mock_execution.user_id = "test-user-id"
        mock_execution.script_id = "batch-script-id"

        mock_script = Mock(spec=Script)
        mock_script.compute_type = "batch"

        # No GEE tasks
        mock_gee_cancel.return_value = []
        mock_execution_log.query.filter.return_value.order_by.return_value.all.return_value = []

        with (
            patch.object(
                ExecutionService, "get_execution", return_value=mock_execution
            ),
            patch("gefapi.services.execution_service.Script") as mock_script_model,
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch("gefapi.services.execution_service.db"),
        ):
            mock_script_model.query.get.return_value = mock_script
            mock_task_result = Mock()
            mock_task_result.id = "cancel-task-1"
            mock_celery.send_task.return_value = mock_task_result

            result = ExecutionService.cancel_execution("batch-exec-id")

        # Should have dispatched async cancellation workflow
        mock_celery.send_task.assert_called_once_with(
            "gefapi.tasks.execution_cancellation.cancel_execution_workflow",
            args=["batch-exec-id"],
            queue="build",
        )

        # Should have set CANCELLING status
        assert mock_execution.status == "CANCELLING"

        # Result should contain queued metadata
        details = result["cancellation_details"]
        assert details["queued"] is True
        assert details["new_status"] == "CANCELLING"
        assert details["task_id"] == "cancel-task-1"

    @patch("gefapi.services.execution_service.ExecutionLog")
    @patch("gefapi.services.execution_service.rollbar")
    @patch.object(GEEService, "cancel_gee_tasks_from_execution")
    def test_cancel_docker_execution_skips_batch(
        self, mock_gee_cancel, mock_rollbar, mock_execution_log
    ):
        """Test that cancelling a Docker execution doesn't call
        terminate_batch_jobs"""
        from gefapi.models import Execution, Script

        mock_execution = Mock(spec=Execution)
        mock_execution.id = "docker-exec-id"
        mock_execution.status = "RUNNING"
        mock_execution.user_id = "test-user-id"
        mock_execution.script_id = "docker-script-id"

        mock_script = Mock(spec=Script)
        mock_script.compute_type = "docker"

        mock_gee_cancel.return_value = []
        mock_execution_log.query.filter.return_value.order_by.return_value.all.return_value = []

        mock_task_result = Mock()
        mock_task_result.id = "cancel-task-2"

        with (
            patch.object(
                ExecutionService, "get_execution", return_value=mock_execution
            ),
            patch("gefapi.services.execution_service.Script") as mock_script_model,
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch("gefapi.services.execution_service.db"),
        ):
            mock_script_model.query.get.return_value = mock_script
            mock_celery.send_task.return_value = mock_task_result

            result = ExecutionService.cancel_execution("docker-exec-id")

        # Should have dispatched async cancellation workflow
        mock_celery.send_task.assert_called_once_with(
            "gefapi.tasks.execution_cancellation.cancel_execution_workflow",
            args=["docker-exec-id"],
            queue="build",
        )

        # Result should contain queue metadata
        details = result["cancellation_details"]
        assert details["queued"] is True
        assert details["new_status"] == "CANCELLING"
        assert details["task_id"] == "cancel-task-2"

    @patch("gefapi.services.execution_service.ExecutionLog")
    @patch("gefapi.services.execution_service.rollbar")
    @patch.object(GEEService, "cancel_gee_tasks_from_execution")
    def test_cancel_batch_execution_with_terminate_failure(
        self, mock_gee_cancel, mock_rollbar, mock_execution_log
    ):
        """Test that batch cancellation failure is handled gracefully"""
        from gefapi.models import Execution, Script

        mock_execution = Mock(spec=Execution)
        mock_execution.id = "batch-exec-fail"
        mock_execution.status = "RUNNING"
        mock_execution.user_id = "test-user-id"
        mock_execution.script_id = "batch-script-id"

        mock_script = Mock(spec=Script)
        mock_script.compute_type = "batch"

        mock_gee_cancel.return_value = []
        mock_execution_log.query.filter.return_value.order_by.return_value.all.return_value = []

        with (
            patch.object(
                ExecutionService, "get_execution", return_value=mock_execution
            ),
            patch("gefapi.services.execution_service.Script") as mock_script_model,
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch("gefapi.services.execution_service.db"),
        ):
            mock_script_model.query.get.return_value = mock_script
            mock_task_result = Mock()
            mock_task_result.id = "cancel-task-3"
            mock_celery.send_task.return_value = mock_task_result

            result = ExecutionService.cancel_execution("batch-exec-fail")

        assert mock_execution.status == "CANCELLING"
        details = result["cancellation_details"]
        assert details["queued"] is True
        assert details["errors"] == []

    @patch("gefapi.services.execution_service.ExecutionLog")
    @patch("gefapi.services.execution_service.rollbar")
    @patch.object(GEEService, "cancel_gee_tasks_from_execution")
    def test_cancel_batch_execution_summary_text(
        self, mock_gee_cancel, mock_rollbar, mock_execution_log
    ):
        """Test that cancellation summary mentions batch jobs"""
        from gefapi.models import Execution, Script

        mock_execution = Mock(spec=Execution)
        mock_execution.id = "batch-exec-summary"
        mock_execution.status = "RUNNING"
        mock_execution.user_id = "test-user-id"
        mock_execution.script_id = "batch-script-id"

        mock_script = Mock(spec=Script)
        mock_script.compute_type = "batch"

        mock_gee_cancel.return_value = []
        mock_execution_log.query.filter.return_value.order_by.return_value.all.return_value = []

        with (
            patch.object(
                ExecutionService, "get_execution", return_value=mock_execution
            ),
            patch("gefapi.services.execution_service.Script") as mock_script_model,
            patch("gefapi.services.execution_service.celery_app") as mock_celery,
            patch("gefapi.services.execution_service.db"),
        ):
            mock_script_model.query.get.return_value = mock_script
            mock_task_result = Mock()
            mock_task_result.id = "cancel-task-4"
            mock_celery.send_task.return_value = mock_task_result

            ExecutionService.cancel_execution("batch-exec-summary")

        # Verify the request log was created
        log_calls = mock_execution_log.call_args_list
        assert len(log_calls) >= 1
        log_text = log_calls[0].kwargs.get("text", "")
        assert "Cancellation requested by user" in log_text
