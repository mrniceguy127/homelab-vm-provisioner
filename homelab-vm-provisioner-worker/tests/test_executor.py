"""Tests for worker job executor."""

import unittest
from unittest.mock import Mock, patch

from hlvmp_worker.executor import JobExecutionError, JobExecutor


class TestJobExecutor(unittest.TestCase):
    """Test job executor for provisioner operations."""

    def test_init_raises_when_cli_path_missing(self):
        """Test executor initialization fails without provisioner path."""
        with (
            patch("hlvmp_worker.executor.Path.exists", return_value=False),
            self.assertRaises(ValueError) as context,
        ):
            JobExecutor("/fake/path/to/cli", Mock())

        self.assertIn("does not exist", str(context.exception))

    def setUp(self):
        """Set up test fixtures."""
        self.db_client = Mock()
        self.service_mode = Mock()
        self.service_mode.create_vm.return_value = {"state": {"vm_name": "test-vm"}}
        self.service_mode.clone_vm.return_value = {"state": {"vm_name": "test-vm"}}
        self.service_mode.create_snapshot_record.return_value = {"snapshot_id": "snap-1"}
        self.service_mode.restore_snapshot_record.return_value = {"runtime_state": {"vm_name": "test-vm"}}
        self.service_mode.delete_snapshot_record.return_value = {"snapshot_id": "snap-1"}
        with patch("hlvmp_worker.executor.Path.exists", return_value=True), patch.object(
            JobExecutor, "_load_service_mode_module", return_value=self.service_mode
        ):
            self.executor = JobExecutor("/fake/path/to/cli", self.db_client)
        self.db_client.get_vm_definition_by_name.return_value = {
            "id": 42,
            "vm_name": "test-vm",
            "config": {
                "vm": {"name": "test-vm", "user": "tester"},
                "network": {"mode": "nat-auto"},
            },
        }
        self.db_client.get_vm_runtime_state.return_value = None
        self.db_client.list_vm_definitions.return_value = []
        self.db_client.list_vm_runtime_states.return_value = []
        self.db_client.list_network_groups.return_value = []
        self.db_client.get_vm_snapshot.return_value = {
            "vm_name": "test-vm",
            "snapshot_id": "snap-1",
            "metadata": {
                "snapshot_id": "snap-1",
                "artifact_manifest": {"snapshot_path": "/snapshots/test-vm/snap-1", "disk": "/snapshots/test-vm/snap-1/test-vm.qcow2"},
                "config_snapshot": {"vm": {"name": "test-vm", "user": "tester"}},
                "runtime_state_snapshot": {},
            },
        }

    def test_get_supported_job_types(self):
        """Test getting list of supported job types."""
        job_types = self.executor.get_supported_job_types()

        self.assertIn("provision_vm", job_types)
        self.assertIn("destroy_vm", job_types)
        self.assertIn("clone_vm", job_types)
        self.assertIn("start_vm", job_types)
        self.assertIn("stop_vm", job_types)
        self.assertIn("reconcile_vm_networking", job_types)

    def test_get_resource_locks_for_vm_job(self):
        """Test resource lock calculation for VM-specific jobs."""
        job = {
            "type": "provision_vm",
            "targetHostId": "host1",
            "targetVmId": "test-vm",
        }

        locks = self.executor.get_resource_locks(job)

        self.assertEqual(locks, ["vm:test-vm"])

    def test_get_resource_locks_for_vm_job_no_vm_id(self):
        """Test resource lock fallback when VM ID is missing."""
        job = {
            "type": "provision_vm",
            "targetHostId": "host1",
            "targetVmId": None,
        }

        locks = self.executor.get_resource_locks(job)

        self.assertEqual(locks, ["host:host1"])

    def test_get_resource_locks_for_networking_job(self):
        """Test resource lock calculation for networking jobs."""
        job = {
            "type": "reconcile_vm_networking",
            "targetHostId": "host1",
        }

        locks = self.executor.get_resource_locks(job)

        # Should be sorted: firewall before network
        self.assertEqual(locks, ["firewall:host1", "network:host1"])

    def test_get_resource_locks_for_multiple_vms(self):
        """Test that locks are sorted for deterministic ordering."""
        job1 = {
            "type": "start_vm",
            "targetHostId": "host1",
            "targetVmId": "vm-a",
        }
        job2 = {
            "type": "start_vm",
            "targetHostId": "host1",
            "targetVmId": "vm-b",
        }

        locks1 = self.executor.get_resource_locks(job1)
        locks2 = self.executor.get_resource_locks(job2)

        # Each should have exactly one lock
        self.assertEqual(len(locks1), 1)
        self.assertEqual(len(locks2), 1)
        self.assertNotEqual(locks1, locks2)

    def test_get_resource_locks_for_unknown_job_uses_host_lock(self):
        """Test unknown job types fall back to a host-level lock."""
        job = {
            "type": "custom_job",
            "targetHostId": "host1",
        }

        locks = self.executor.get_resource_locks(job)

        self.assertEqual(locks, ["host:host1"])

    def test_load_service_mode_module_missing_package_raises_job_execution_error(self):
        with patch("hlvmp_worker.executor.Path.exists", return_value=True), patch(
            "hlvmp_worker.executor.import_module", side_effect=ModuleNotFoundError("missing")
        ), self.assertRaises(JobExecutionError) as context:
            JobExecutor("/fake/path/to/cli", Mock())

        self.assertIn("Could not import provisioner service module", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_provision_vm_success(self):
        """Test successful VM provision execution."""
        job = {
            "type": "provision_vm",
            "payload": {"vmName": "test-vm"},
        }

        result = self.executor.execute_job(job)

        self.assertTrue(result["success"])
        self.assertEqual(result["vmName"], "test-vm")
        self.service_mode.create_vm.assert_called_once()

    def test_execute_provision_vm_missing_name(self):
        """Test provision VM with missing VM name."""
        job = {
            "type": "provision_vm",
            "payload": {},
        }

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(job)

        self.assertIn("vmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_provision_vm_definition_not_found(self):
        """Test provision VM when definition doesn't exist."""
        self.db_client.get_vm_definition_by_name.return_value = None
        job = {
            "type": "provision_vm",
            "payload": {"vmName": "missing-vm"},
        }

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(job)

        self.assertIn("definition not found", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_destroy_vm_success(self):
        """Test successful VM destroy execution."""
        job = {
            "type": "destroy_vm",
            "payload": {"vmName": "test-vm"},
        }

        result = self.executor.execute_job(job)

        self.assertTrue(result["success"])
        self.assertEqual(result["vmName"], "test-vm")
        self.service_mode.destroy_vm.assert_called_once_with("test-vm")

    def test_execute_destroy_vm_missing_name(self):
        """Test destroy VM with missing name."""
        job = {
            "type": "destroy_vm",
            "payload": {},
        }

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(job)

        self.assertIn("vmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_clone_vm_success(self):
        """Test successful VM clone execution."""
        job = {
            "type": "clone_vm",
            "payload": {
                "sourceVmName": "source-vm",
                "targetVmName": "test-vm",
            },
        }

        result = self.executor.execute_job(job)

        self.assertTrue(result["success"])
        self.assertEqual(result["sourceVmName"], "source-vm")
        self.service_mode.clone_vm.assert_called_once()

    def test_execute_clone_vm_missing_source_name(self):
        """Test clone VM requires a source VM name."""
        job = {
            "type": "clone_vm",
            "payload": {"configPath": "/tmp/target-config.yaml"},
        }

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(job)

        self.assertIn("sourceVmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_clone_vm_missing_target_name(self):
        """Test clone VM requires a target VM name."""
        job = {
            "type": "clone_vm",
            "payload": {"sourceVmName": "source-vm"},
        }

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(job)

        self.assertIn("targetVmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_clone_vm_definition_not_found(self):
        """Test clone VM validates the target definition exists."""
        self.db_client.get_vm_definition_by_name.return_value = None
        job = {
            "type": "clone_vm",
            "payload": {
                "sourceVmName": "source-vm",
                "targetVmName": "missing-vm",
            },
        }

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(job)

        self.assertIn("definition not found", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_start_vm_success(self):
        """Test successful VM start execution."""
        job = {
            "type": "start_vm",
            "payload": {"vmName": "test-vm"},
        }

        result = self.executor.execute_job(job)

        self.assertTrue(result["success"])
        self.service_mode.start_vm.assert_called_once_with("test-vm")

    def test_execute_start_vm_missing_name(self):
        """Test start VM requires a VM name."""
        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job({"type": "start_vm", "payload": {}})

        self.assertIn("vmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_stop_vm_success(self):
        """Test successful VM stop execution."""
        job = {
            "type": "stop_vm",
            "payload": {"vmName": "test-vm"},
        }

        result = self.executor.execute_job(job)

        self.assertTrue(result["success"])
        self.service_mode.stop_vm.assert_called_once_with("test-vm")

    def test_execute_stop_vm_missing_name(self):
        """Test stop VM requires a VM name."""
        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job({"type": "stop_vm", "payload": {}})

        self.assertIn("vmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_reconcile_networking_success(self):
        """Test successful network reconciliation execution."""
        job = {
            "type": "reconcile_vm_networking",
            "payload": {"policyOnly": True},
        }

        result = self.executor.execute_job(job)

        self.assertTrue(result["success"])
        self.assertTrue(result["policyOnly"])
        self.service_mode.reconcile_vm_records.assert_called_once()
        self.assertTrue(result["policyOnly"])

    def test_execute_reconcile_networking_defaults_policy_only_false(self):
        """Test network reconciliation defaults to full reconcile."""
        result = self.executor.execute_job(
            {"type": "reconcile_vm_networking", "payload": {}}
        )

        self.assertFalse(result["policyOnly"])
        self.service_mode.reconcile_vm_records.assert_called_once()

    def test_execute_snapshot_create_success(self):
        """Test successful snapshot creation execution."""
        job = {
            "type": "snapshot_create",
            "payload": {"vmName": "test-vm"},
        }

        result = self.executor.execute_job(job)

        self.assertTrue(result["success"])
        self.service_mode.create_snapshot_record.assert_called_once()

    def test_execute_snapshot_create_missing_name(self):
        """Test snapshot creation requires a VM name."""
        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job({"type": "snapshot_create", "payload": {}})

        self.assertIn("vmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_snapshot_restore_success(self):
        """Test successful snapshot restore execution."""
        result = self.executor.execute_job(
            {
                "type": "snapshot_restore",
                "payload": {"vmName": "test-vm", "snapshotId": "snap-1"},
            }
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["snapshotId"], "snap-1")
        self.service_mode.restore_snapshot_record.assert_called_once()

    def test_execute_snapshot_restore_missing_snapshot_id(self):
        """Test snapshot restore requires a snapshot ID."""
        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(
                {"type": "snapshot_restore", "payload": {"vmName": "test-vm"}}
            )

        self.assertIn("snapshotId", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_snapshot_delete_success(self):
        """Test successful snapshot deletion execution."""
        result = self.executor.execute_job(
            {
                "type": "snapshot_delete",
                "payload": {"vmName": "test-vm", "snapshotId": "snap-1"},
            }
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["snapshotId"], "snap-1")
        self.service_mode.delete_snapshot_record.assert_called_once()

    def test_execute_snapshot_delete_missing_vm_name(self):
        """Test snapshot deletion requires a VM name."""
        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(
                {"type": "snapshot_delete", "payload": {"snapshotId": "snap-1"}}
            )

        self.assertIn("vmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_snapshot_delete_missing_snapshot_id(self):
        """Test snapshot deletion requires a snapshot ID."""
        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(
                {"type": "snapshot_delete", "payload": {"vmName": "test-vm"}}
            )

        self.assertIn("snapshotId", str(context.exception))
        self.assertFalse(context.exception.retriable)

    def test_execute_job_wraps_unexpected_handler_error(self):
        """Test unexpected handler exceptions are wrapped as retriable errors."""
        self.executor._handlers["explode"] = Mock(side_effect=RuntimeError("boom"))

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job({"type": "explode", "payload": {}})

        self.assertIn("Job execution failed: boom", str(context.exception))
        self.assertTrue(context.exception.retriable)

    def test_execute_unsupported_job_type(self):
        """Test execution with unsupported job type."""
        job = {
            "type": "unsupported_operation",
            "payload": {},
        }

        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job(job)

        self.assertIn("Unsupported", str(context.exception))
        self.assertFalse(context.exception.retriable)

    @patch("hlvmp_worker.executor.Path")
    def test_execute_collect_vm_logs_success(self, mock_path):
        """Test collecting VM logs successfully."""
        mock_log_file = Mock()
        mock_log_file.exists.return_value = True

        mock_file_handle = Mock()
        mock_file_handle.readlines.return_value = [
            "Line 1\n",
            "Line 2\n",
            "Line 3\n",
        ]
        mock_log_file.open.return_value.__enter__ = Mock(return_value=mock_file_handle)
        mock_log_file.open.return_value.__exit__ = Mock(return_value=False)

        mock_path.return_value = mock_log_file

        result = self.executor.execute_job({
            "type": "collect_vm_logs",
            "payload": {"vmName": "test-vm", "lines": 10}
        })

        self.assertEqual(result["vm_name"], "test-vm")
        self.assertEqual(result["lines_collected"], 3)
        self.assertTrue(result["log_exists"])
        self.db_client.store_vm_log_snapshot.assert_called_once()

    @patch("hlvmp_worker.executor.Path")
    def test_execute_collect_vm_logs_no_log_file(self, mock_path):
        """Test collecting VM logs when log file doesn't exist."""
        mock_log_file = Mock()
        mock_log_file.exists.return_value = False
        mock_path.return_value = mock_log_file

        result = self.executor.execute_job({
            "type": "collect_vm_logs",
            "payload": {"vmName": "test-vm"}
        })

        self.assertEqual(result["vm_name"], "test-vm")
        self.assertEqual(result["lines_collected"], 0)
        self.assertFalse(result["log_exists"])

    def test_execute_collect_vm_logs_missing_vm_name(self):
        """Test collecting VM logs without VM name."""
        with self.assertRaises(JobExecutionError) as context:
            self.executor.execute_job({
                "type": "collect_vm_logs",
                "payload": {}
            })

        self.assertIn("vmName", str(context.exception))
        self.assertFalse(context.exception.retriable)

    @patch("hlvmp_worker.executor.Path")
    def test_execute_collect_vm_logs_size_limit(self, mock_path):
        """Test collecting VM logs with size truncation."""
        # Create large log content that exceeds 1MB
        large_line = "x" * 100000  # 100KB line
        mock_log_file = Mock()
        mock_log_file.exists.return_value = True

        mock_file_handle = Mock()
        mock_file_handle.readlines.return_value = [large_line + "\n"] * 20  # 2MB total
        mock_log_file.open.return_value.__enter__ = Mock(return_value=mock_file_handle)
        mock_log_file.open.return_value.__exit__ = Mock(return_value=False)

        mock_path.return_value = mock_log_file

        result = self.executor.execute_job({
            "type": "collect_vm_logs",
            "payload": {"vmName": "test-vm"}
        })

        self.assertEqual(result["vm_name"], "test-vm")
        self.assertTrue(result["log_exists"])
        # Verify truncation happened
        call_args = self.db_client.store_vm_log_snapshot.call_args
        log_content = call_args.kwargs["log_content"]
        self.assertLessEqual(len(log_content.encode("utf-8")), 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
