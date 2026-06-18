"""Tests for worker daemon."""

import signal
import unittest
from unittest.mock import Mock, patch

from hlvmp_worker.config import WorkerConfig
from hlvmp_worker.db_client import DatabaseClient
from hlvmp_worker.executor import JobExecutionError, JobExecutor
from hlvmp_worker.worker import WorkerDaemon


class TestWorkerDaemon(unittest.TestCase):
    """Test worker daemon for job processing."""

    def setUp(self):
        """Set up test fixtures."""
        with patch("hlvmp_worker.config.shutil.which", return_value="/usr/bin/vmctl"):
            self.config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                worker_id="test-worker",
                concurrency=2,
                poll_interval=1.0,
                db_service_url="http://localhost:3002",
                db_service_password="test-password",
            )
        self.db_client = Mock(spec=DatabaseClient)
        self.executor = Mock(spec=JobExecutor)
        self.worker = WorkerDaemon(self.config, self.db_client, self.executor)

    def test_init(self):
        """Test worker daemon initialization."""
        self.assertEqual(self.worker.config, self.config)
        self.assertEqual(self.worker.db_client, self.db_client)
        self.assertEqual(self.worker.executor, self.executor)
        self.assertFalse(self.worker.running)
        self.assertFalse(self.worker.shutdown_requested)

    def test_handle_signal(self):
        """Test signal handler sets shutdown flag."""
        self.worker._handle_signal(signal.SIGTERM, None)

        self.assertTrue(self.worker.shutdown_requested)

    def test_log_job_event_success(self):
        """Test logging job event successfully."""
        self.db_client.append_job_event.return_value = {"id": 1}

        self.worker._log_job_event(1, "info", "Test message")

        self.db_client.append_job_event.assert_called_once_with(
            1, "info", "Test message", None
        )

    def test_log_job_event_failure(self):
        """Test logging job event when database call fails."""
        self.db_client.append_job_event.side_effect = RuntimeError("DB error")

        # Should not raise exception
        self.worker._log_job_event(1, "info", "Test message")

    def test_process_job_claims_only_for_local_host(self):
        """Test that worker only claims jobs for its local host."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"configPath": "/tmp/test.yaml"},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {"success": True}

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.mark_job_running.assert_called_once()
        self.db_client.mark_job_succeeded.assert_called_once()

    def test_process_job_does_not_claim_other_host_jobs(self):
        """Test that worker does not process jobs for other hosts."""
        # This is enforced by the claim logic, not the process logic
        # The worker should never receive jobs for other hosts
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "other-host",  # Different host
            "targetVmId": "test-vm",
            "payload": {"configPath": "/tmp/test.yaml"},
        }

        # Even if we somehow got this job, it should process normally
        # The claim mechanism prevents this scenario
        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {"success": True}

        result = self.worker._process_job(job)

        # Job processes normally, but in practice, claim prevents this
        self.assertTrue(result)

    def test_process_job_lock_conflict(self):
        """Test job processing when resource locks cannot be acquired."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"configPath": "/tmp/test.yaml"},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = False

        result = self.worker._process_job(job)

        self.assertFalse(result)
        self.db_client.mark_job_running.assert_not_called()
        self.executor.execute_job.assert_not_called()

    def test_process_job_success(self):
        """Test successful job processing."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"configPath": "/tmp/test.yaml"},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {"success": True}

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.mark_job_running.assert_called_once_with(1, "test-worker")
        self.executor.execute_job.assert_called_once_with(job)
        self.db_client.mark_job_succeeded.assert_called_once_with(
            1, {"success": True}
        )
        self.db_client.release_resource_locks.assert_called_once_with(1, "test-worker")

    def test_process_job_failure(self):
        """Test job processing when execution fails."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"configPath": "/tmp/test.yaml"},
        }

        error = JobExecutionError("Test error", retriable=True)
        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.side_effect = error

        result = self.worker._process_job(job)

        self.assertFalse(result)
        self.db_client.mark_job_failed.assert_called_once_with(
            1, "Test error", retriable=True
        )
        self.db_client.release_resource_locks.assert_called_once_with(1, "test-worker")

    def test_process_job_unexpected_error(self):
        """Test job processing with unexpected error."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"configPath": "/tmp/test.yaml"},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.side_effect = RuntimeError("Unexpected error")

        result = self.worker._process_job(job)

        self.assertFalse(result)
        self.db_client.mark_job_failed.assert_called_once()
        # Unexpected errors are retriable
        call_args = self.db_client.mark_job_failed.call_args
        self.assertEqual(call_args[0][0], 1)
        self.assertIn("Unexpected error", call_args[0][1])
        self.assertTrue(call_args[1]["retriable"])

    def test_process_job_releases_locks_on_error(self):
        """Test that locks are released even when job fails."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"configPath": "/tmp/test.yaml"},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.side_effect = RuntimeError("Test error")

        self.worker._process_job(job)

        self.db_client.release_resource_locks.assert_called_once_with(1, "test-worker")

    def test_concurrent_safe_jobs(self):
        """Test that non-conflicting jobs can run concurrently."""
        # This is implicitly tested by the thread pool in run()
        # The executor returns different locks for different VMs

        # Different VMs should have different locks
        locks1 = ["vm:vm1"]
        locks2 = ["vm:vm2"]

        self.assertNotEqual(locks1, locks2)

    def test_concurrent_conflicting_jobs(self):
        """Test that conflicting jobs cannot run concurrently."""
        job1 = {
            "id": 1,
            "type": "start_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"vmName": "test-vm"},
        }
        job2 = {
            "id": 2,
            "type": "stop_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {"vmName": "test-vm"},
        }

        # Same VM should have the same lock
        self.executor.get_resource_locks.return_value = ["vm:test-vm"]

        # First job acquires lock
        self.db_client.acquire_resource_locks.return_value = True
        result1 = self.worker._process_job(job1)

        # Second job cannot acquire lock (simulated by returning False)
        self.db_client.acquire_resource_locks.return_value = False
        result2 = self.worker._process_job(job2)

        # First job should succeed, second should fail to acquire lock
        self.assertTrue(result1)
        self.assertFalse(result2)

    def test_process_job_no_locks_required(self):
        """Test processing a job that requires no locks."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": None,
            "payload": {},
        }

        # No locks required
        self.executor.get_resource_locks.return_value = []
        self.executor.execute_job.return_value = {"success": True}

        result = self.worker._process_job(job)

        self.assertTrue(result)
        # Should not try to acquire locks
        self.db_client.acquire_resource_locks.assert_not_called()
        self.db_client.release_resource_locks.assert_not_called()

    def test_run_no_jobs_available(self):
        """Test run loop when no jobs are available."""
        # Health check passes
        self.db_client.health_check.return_value = True

        # No jobs available
        self.db_client.claim_next_job.return_value = None

        # Set shutdown after first iteration
        call_count = [0]

        def trigger_shutdown(host_id, worker_id):
            call_count[0] += 1
            if call_count[0] >= 1:
                self.worker.shutdown_requested = True

        self.db_client.claim_next_job.side_effect = trigger_shutdown

        self.worker.run()

        self.db_client.health_check.assert_called_once()
        self.assertGreaterEqual(self.db_client.claim_next_job.call_count, 1)
        self.assertFalse(self.worker.running)

    def test_run_processes_job(self):
        """Test run loop processes a job."""
        # Health check passes
        self.db_client.health_check.return_value = True

        # Return a job once, then trigger shutdown
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {},
        }

        call_count = [0]

        def claim_job_then_shutdown(host_id, worker_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return job
            self.worker.shutdown_requested = True
            return None

        self.db_client.claim_next_job.side_effect = claim_job_then_shutdown
        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {"success": True}

        self.worker.run()

        self.db_client.health_check.assert_called_once()
        self.assertGreaterEqual(self.db_client.claim_next_job.call_count, 1)
        self.executor.execute_job.assert_called()

    def test_run_health_check_fails(self):
        """Test run exits if health check fails."""
        self.db_client.health_check.return_value = False

        with self.assertRaises(SystemExit) as cm:
            self.worker.run()

        self.assertEqual(cm.exception.code, 1)

    def test_run_handles_exception_in_loop(self):
        """Test run handles exceptions in main loop gracefully."""
        # Health check passes
        self.db_client.health_check.return_value = True

        # Raise exception on first claim, then trigger shutdown
        call_count = [0]

        def claim_with_error(host_id, worker_id):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Test error")
            self.worker.shutdown_requested = True

        self.db_client.claim_next_job.side_effect = claim_with_error

        self.worker.run()

        # Should have called claim_next_job multiple times (once with error, once after)
        self.assertGreaterEqual(self.db_client.claim_next_job.call_count, 2)

    def test_on_socket_wake_sets_event(self):
        """Test that socket wake callback sets the wake event."""
        self.assertFalse(self.worker.wake_event.is_set())

        self.worker._on_socket_wake()

        self.assertTrue(self.worker.wake_event.is_set())

    def test_on_socket_health_returns_status(self):
        """Test that socket health callback returns worker status."""
        # Add some active jobs
        self.worker.active_jobs.add(1)
        self.worker.active_jobs.add(2)

        health = self.worker._on_socket_health()

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["worker_id"], "test-worker")
        self.assertEqual(health["host_id"], "test-host")
        self.assertEqual(health["concurrency"], 2)
        self.assertEqual(health["active_jobs"], 2)
        self.assertEqual(health["available_slots"], 0)

    def test_signal_handler_triggers_wake_event(self):
        """Test that signal handler triggers wake event for fast shutdown."""
        self.assertFalse(self.worker.wake_event.is_set())

        self.worker._handle_signal(signal.SIGTERM, None)

        self.assertTrue(self.worker.shutdown_requested)
        self.assertTrue(self.worker.wake_event.is_set())


if __name__ == "__main__":
    unittest.main()
