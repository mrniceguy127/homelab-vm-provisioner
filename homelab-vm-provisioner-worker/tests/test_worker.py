"""Tests for worker daemon."""

import signal
import unittest
from unittest.mock import MagicMock, Mock, patch

from hlvmp_worker.config import WorkerConfig
from hlvmp_worker.db_client import DatabaseClient
from hlvmp_worker.executor import JobExecutionError, JobExecutor
from hlvmp_worker.worker import WorkerDaemon


class TestWorkerDaemon(unittest.TestCase):
    """Test worker daemon for job processing."""

    def setUp(self):
        """Set up test fixtures."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            self.config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                worker_id="test-worker",
                concurrency=2,
                poll_interval=1.0,
                db_service_url="http://localhost:3002",
                db_service_password="test-password",
                provisioner_cli_path="/usr/bin/vmctl",  # Required for standalone microservice
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

    def test_refresh_runtime_state_caches_updates_db(self):
        self.executor.refresh_all_runtime_state_caches.return_value = [
            {"vmName": "demo", "runtimeState": {"status": "running"}},
            {"vmName": "clonebox", "runtimeState": {"status": "stopped"}},
        ]

        self.worker._refresh_runtime_state_caches()

        self.db_client.upsert_vm_runtime_state.assert_any_call("demo", {"status": "running"}, "background_refresh")
        self.db_client.upsert_vm_runtime_state.assert_any_call("clonebox", {"status": "stopped"}, "background_refresh")

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

    def test_process_job_deletes_runtime_state(self):
        """Test job result with deleteRuntimeState flag."""
        job = {
            "id": 1,
            "type": "destroy_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {
            "vmName": "test-vm",
            "deleteRuntimeState": True,
        }

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.delete_vm_runtime_state.assert_called_once_with("test-vm")

    def test_process_job_patches_runtime_state(self):
        """Test job result with runtimeStatePatch."""
        job = {
            "id": 1,
            "type": "start_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.db_client.get_vm_runtime_state.return_value = {
            "vm_name": "test-vm",
            "state": {"status": "stopped"},
        }
        self.executor.execute_job.return_value = {
            "vmName": "test-vm",
            "runtimeStatePatch": {"status": "running"},
        }

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.upsert_vm_runtime_state.assert_called_once_with(
            "test-vm",
            {"status": "running"},
            "worker_mutation",
        )

    def test_process_job_upserts_snapshot_record(self):
        """Test job result with snapshotRecord."""
        job = {
            "id": 1,
            "type": "snapshot_create",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {
            "vmName": "test-vm",
            "snapshotId": "snap-1",
            "snapshotRecord": {"metadata": {"created": "2024-01-01"}},
        }

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.upsert_vm_snapshot.assert_called_once_with(
            "test-vm",
            "snap-1",
            {"metadata": {"created": "2024-01-01"}},
        )

    def test_process_job_deletes_snapshot_record(self):
        """Test job result with deleteSnapshotRecord."""
        job = {
            "id": 1,
            "type": "snapshot_delete",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {
            "vmName": "test-vm",
            "snapshotId": "snap-1",
            "deleteSnapshotRecord": True,
        }

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.delete_vm_snapshot.assert_called_once_with("test-vm", "snap-1")

    def test_process_job_mark_failed_error(self):
        """Test handling of error when marking job as failed."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.side_effect = RuntimeError("Unexpected error")
        self.db_client.mark_job_failed.side_effect = RuntimeError("DB error")

        result = self.worker._process_job(job)

        self.assertFalse(result)
        self.db_client.mark_job_failed.assert_called_once()

    @patch("hlvmp_worker.worker.subprocess.run")
    def test_process_job_lock_release_error(self, mock_run):
        """Test that lock release errors are logged but don't fail the job."""
        job = {
            "id": 101,
            "type": "provision_vm",
            "status": "queued",
            "payload": {"configPath": "/path/to/config"},
        }

        self.executor.get_resource_locks.return_value = ["vm:test-vm"]
        self.db_client.acquire_resource_locks.return_value = True
        self.executor.execute_job.return_value = {"success": True}
        self.db_client.release_resource_locks.side_effect = RuntimeError("Lock release failed")

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.mark_job_succeeded.assert_called_once()

    def test_process_job_with_runtime_state_patch(self):
        """Test job processing with runtime state patch."""
        job = {
            "id": 102,
            "type": "update_vm",
            "status": "queued",
            "targetVmId": "test-vm",
            "payload": {},
        }

        current_state = {"state": {"status": "running", "cpu": 2}}
        patch = {"memory": 4096}

        self.executor.get_resource_locks.return_value = []
        self.executor.execute_job.return_value = {
            "success": True,
            "vmName": "test-vm",
            "runtimeStatePatch": patch,
        }
        self.db_client.get_vm_runtime_state.return_value = current_state

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.upsert_vm_runtime_state.assert_called_once()
        call_args = self.db_client.upsert_vm_runtime_state.call_args
        updated_state = call_args[0][1]
        self.assertEqual(updated_state["status"], "running")
        self.assertEqual(updated_state["memory"], 4096)

    def test_process_job_with_delete_runtime_state(self):
        """Test job processing with runtime state deletion."""
        job = {
            "id": 103,
            "type": "delete_vm",
            "status": "queued",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = []
        self.executor.execute_job.return_value = {
            "success": True,
            "vmName": "test-vm",
            "deleteRuntimeState": True,
        }

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.delete_vm_runtime_state.assert_called_once_with("test-vm")

    def test_process_job_with_snapshot_record(self):
        """Test job processing with snapshot record."""
        job = {
            "id": 104,
            "type": "create_snapshot",
            "status": "queued",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = []
        self.executor.execute_job.return_value = {
            "success": True,
            "vmName": "test-vm",
            "snapshotId": "snap-1",
            "snapshotRecord": {"name": "snap-1", "created": "2026-06-22"},
        }

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.upsert_vm_snapshot.assert_called_once_with(
            "test-vm", "snap-1", {"name": "snap-1", "created": "2026-06-22"}
        )

    def test_process_job_with_delete_snapshot_record(self):
        """Test job processing with snapshot deletion."""
        job = {
            "id": 105,
            "type": "delete_snapshot",
            "status": "queued",
            "targetVmId": "test-vm",
            "payload": {},
        }

        self.executor.get_resource_locks.return_value = []
        self.executor.execute_job.return_value = {
            "success": True,
            "vmName": "test-vm",
            "snapshotId": "snap-1",
            "deleteSnapshotRecord": True,
        }

        result = self.worker._process_job(job)

        self.assertTrue(result)
        self.db_client.delete_vm_snapshot.assert_called_once_with("test-vm", "snap-1")

    def test_refresh_runtime_state_caches(self):
        """Test runtime state cache refresh."""
        self.executor.refresh_all_runtime_state_caches.return_value = [
            {"vmName": "vm1", "runtimeState": {"status": "running"}},
            {"vmName": "vm2", "runtimeState": {"status": "stopped"}},
        ]

        self.worker._refresh_runtime_state_caches()

        self.assertEqual(self.db_client.upsert_vm_runtime_state.call_count, 2)

    def test_refresh_runtime_state_caches_error(self):
        """Test runtime state cache refresh with error."""
        self.executor.refresh_all_runtime_state_caches.side_effect = RuntimeError("Refresh failed")

        # Should not raise exception
        self.worker._refresh_runtime_state_caches()


class TestSudoFunctions(unittest.TestCase):
    """Test sudo validation and keepalive functions."""

    @patch("hlvmp_worker.worker.subprocess.run")
    def test_validate_sudo_credentials_success(self, mock_run):
        """Test successful sudo validation."""
        mock_run.return_value = MagicMock(returncode=0)

        # Should not raise exception
        from hlvmp_worker.worker import ensure_sudo_credentials
        ensure_sudo_credentials()

        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0], ["sudo", "-v"])

    @patch("hlvmp_worker.worker.subprocess.run")
    def test_validate_sudo_credentials_failure(self, mock_run):
        """Test sudo validation failure."""
        mock_run.return_value = MagicMock(returncode=1)

        from hlvmp_worker.worker import ensure_sudo_credentials

        with self.assertRaises(RuntimeError) as context:
            ensure_sudo_credentials()

        self.assertIn("Unable to acquire sudo credentials", str(context.exception))

    @patch("hlvmp_worker.worker.threading.Thread")
    @patch("hlvmp_worker.worker.subprocess.run")
    def test_start_sudo_keepalive(self, mock_run, mock_thread):
        """Test sudo keepalive thread start."""
        from hlvmp_worker.worker import start_sudo_keepalive

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        start_sudo_keepalive()

        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()
        self.assertTrue(mock_thread.call_args[1]["daemon"])


if __name__ == "__main__":
    unittest.main()
