"""Tests for worker job validator."""

import unittest
from unittest.mock import Mock, patch

from hlvmp_worker.validator import (
    JobValidator,
    ValidationErrorCode,
    ValidationResult,
    ValidationStatus,
    WorkerAction,
)


class TestValidationResult(unittest.TestCase):
    """Test validation result model."""

    def test_valid_result(self):
        """Test valid result properties."""
        result = ValidationResult.valid(job_id=1, target="test-vm")

        self.assertTrue(result.is_valid)
        self.assertTrue(result.should_execute)
        self.assertFalse(result.should_noop)
        self.assertFalse(result.should_fail)
        self.assertFalse(result.requires_cleanup)
        self.assertEqual(result.status, ValidationStatus.VALID)
        self.assertEqual(result.action, WorkerAction.PROCEED)

    def test_noop_success_result(self):
        """Test no-op success result properties."""
        result = ValidationResult.noop_success(
            code=ValidationErrorCode.VM_ALREADY_RUNNING,
            reason="VM is already running",
            job_id=1,
            target="test-vm",
        )

        self.assertFalse(result.is_valid)
        self.assertFalse(result.should_execute)
        self.assertTrue(result.should_noop)
        self.assertFalse(result.should_fail)
        self.assertFalse(result.requires_cleanup)
        self.assertEqual(result.status, ValidationStatus.NOOP_SUCCESS)
        self.assertEqual(result.action, WorkerAction.NOOP_SUCCESS)

    def test_invalid_payload_result(self):
        """Test invalid payload result properties."""
        result = ValidationResult.invalid_payload(
            code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
            reason="Missing vmName",
            job_id=1,
        )

        self.assertFalse(result.is_valid)
        self.assertFalse(result.should_execute)
        self.assertFalse(result.should_noop)
        self.assertTrue(result.should_fail)
        self.assertFalse(result.requires_cleanup)
        self.assertEqual(result.status, ValidationStatus.INVALID_PAYLOAD)
        self.assertEqual(result.action, WorkerAction.FAIL)

    def test_cleanup_required_result(self):
        """Test cleanup required result properties."""
        result = ValidationResult.cleanup_required(
            code=ValidationErrorCode.PARTIAL_STATE_DETECTED,
            reason="Partial VM state detected",
            job_id=1,
            target="test-vm",
        )

        self.assertFalse(result.is_valid)
        self.assertFalse(result.should_execute)
        self.assertFalse(result.should_noop)
        self.assertFalse(result.should_fail)
        self.assertTrue(result.requires_cleanup)
        self.assertEqual(result.status, ValidationStatus.CLEANUP_REQUIRED)
        self.assertEqual(result.action, WorkerAction.CLEANUP_REQUIRED)

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = ValidationResult.invalid_payload(
            code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
            reason="Missing vmName",
            job_id=1,
            target="test-vm",
        )

        result_dict = result.to_dict()

        self.assertEqual(result_dict["status"], "invalid_payload")
        self.assertEqual(result_dict["action"], "fail")
        self.assertEqual(result_dict["code"], "MISSING_REQUIRED_FIELD")
        self.assertEqual(result_dict["reason"], "Missing vmName")
        self.assertEqual(result_dict["job_id"], 1)
        self.assertEqual(result_dict["target"], "test-vm")


class TestJobValidator(unittest.TestCase):
    """Test job validator for worker operations."""

    def setUp(self):
        """Set up test fixtures."""
        self.worker_config = Mock()
        self.worker_config.host_id = "test-host-1"
        self.db_client = Mock()
        self.service_mode = Mock()
        self.validator = JobValidator(self.worker_config, self.db_client, self.service_mode)

    def test_validate_target_host_valid(self):
        """Test host validation passes for correct host."""
        job = {"id": 1, "type": "provision_vm", "targetHostId": "test-host-1", "payload": {}}

        result = self.validator.validate_target_host(job)

        self.assertTrue(result.is_valid)

    def test_validate_target_host_mismatch(self):
        """Test host validation fails for wrong host."""
        job = {"id": 1, "type": "provision_vm", "targetHostId": "wrong-host", "payload": {}}

        result = self.validator.validate_target_host(job)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.WRONG_HOST)
        self.assertEqual(result.code, ValidationErrorCode.HOST_MISMATCH)
        self.assertIn("wrong-host", result.reason)
        self.assertIn("test-host-1", result.reason)

    def test_validate_target_host_missing(self):
        """Test host validation fails when targetHostId is missing."""
        job = {"id": 1, "type": "provision_vm", "payload": {}}

        result = self.validator.validate_target_host(job)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.INVALID_PAYLOAD)
        self.assertEqual(result.code, ValidationErrorCode.MISSING_REQUIRED_FIELD)

    def test_validate_job_payload_valid(self):
        """Test payload validation passes for valid payload."""
        job = {"id": 1, "type": "provision_vm", "payload": {"vmName": "test-vm"}}

        result = self.validator.validate_job_payload(job)

        self.assertTrue(result.is_valid)

    def test_validate_job_payload_missing(self):
        """Test payload validation fails when payload is missing."""
        job = {"id": 1, "type": "provision_vm"}

        result = self.validator.validate_job_payload(job)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.INVALID_PAYLOAD)

    def test_validate_job_payload_not_dict(self):
        """Test payload validation fails when payload is not a dict."""
        job = {"id": 1, "type": "provision_vm", "payload": "not-a-dict"}

        result = self.validator.validate_job_payload(job)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.INVALID_PAYLOAD)
        self.assertIn("dictionary", result.reason)

    def test_validate_provision_vm_success(self):
        """Test provision VM validation succeeds when VM doesn't exist."""
        self.db_client.get_vm_definition_by_name.return_value = {
            "id": 1,
            "vm_name": "test-vm",
            "config": {},
        }

        with (
            patch.object(self.validator, "_vm_exists", return_value=False),
            patch.object(self.validator, "_disk_exists", return_value=False),
        ):
            result = self.validator.validate_provision_vm({"vmName": "test-vm"}, job_id=1)

        self.assertTrue(result.is_valid)

    def test_validate_provision_vm_missing_name(self):
        """Test provision VM validation fails without vmName."""
        result = self.validator.validate_provision_vm({}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.code, ValidationErrorCode.MISSING_REQUIRED_FIELD)

    def test_validate_provision_vm_definition_not_found(self):
        """Test provision VM validation fails when definition missing."""
        self.db_client.get_vm_definition_by_name.return_value = None

        result = self.validator.validate_provision_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.code, ValidationErrorCode.VM_DEFINITION_NOT_FOUND)

    def test_validate_provision_vm_already_exists(self):
        """Test provision VM validation fails when VM already exists."""
        self.db_client.get_vm_definition_by_name.return_value = {
            "id": 1,
            "vm_name": "test-vm",
            "config": {},
        }

        with (
            patch.object(self.validator, "_vm_exists", return_value=True),
            patch.object(self.validator, "_disk_exists", return_value=True),
        ):
            result = self.validator.validate_provision_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.ALREADY_EXISTS)
        self.assertEqual(result.code, ValidationErrorCode.VM_ALREADY_EXISTS)
        self.assertIn("duplicate creation", result.reason)

    def test_validate_provision_vm_partial_state(self):
        """Test provision VM validation detects partial state."""
        self.db_client.get_vm_definition_by_name.return_value = {
            "id": 1,
            "vm_name": "test-vm",
            "config": {},
        }

        # VM exists but disk doesn't
        with (
            patch.object(self.validator, "_vm_exists", return_value=True),
            patch.object(self.validator, "_disk_exists", return_value=False),
        ):
            result = self.validator.validate_provision_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.CLEANUP_REQUIRED)
        self.assertEqual(result.code, ValidationErrorCode.PARTIAL_STATE_DETECTED)
        self.assertTrue(result.requires_cleanup)

    def test_validate_start_vm_success(self):
        """Test start VM validation succeeds for stopped VM."""
        with (
            patch.object(self.validator, "_vm_exists", return_value=True),
            patch.object(self.validator, "_get_vm_status", return_value="shut off"),
        ):
            result = self.validator.validate_start_vm({"vmName": "test-vm"}, job_id=1)

        self.assertTrue(result.is_valid)

    def test_validate_start_vm_not_found(self):
        """Test start VM validation fails when VM doesn't exist."""
        with patch.object(self.validator, "_vm_exists", return_value=False):
            result = self.validator.validate_start_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.NOT_FOUND)
        self.assertEqual(result.code, ValidationErrorCode.VM_NOT_FOUND)

    def test_validate_start_vm_already_running(self):
        """Test start VM validation no-ops when VM already running."""
        with (
            patch.object(self.validator, "_vm_exists", return_value=True),
            patch.object(self.validator, "_get_vm_status", return_value="running"),
        ):
            result = self.validator.validate_start_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertTrue(result.should_noop)
        self.assertEqual(result.status, ValidationStatus.NOOP_SUCCESS)
        self.assertEqual(result.code, ValidationErrorCode.VM_ALREADY_RUNNING)

    def test_validate_stop_vm_success(self):
        """Test stop VM validation succeeds for running VM."""
        with (
            patch.object(self.validator, "_vm_exists", return_value=True),
            patch.object(self.validator, "_get_vm_status", return_value="running"),
        ):
            result = self.validator.validate_stop_vm({"vmName": "test-vm"}, job_id=1)

        self.assertTrue(result.is_valid)

    def test_validate_stop_vm_not_found(self):
        """Test stop VM validation fails when VM doesn't exist."""
        with patch.object(self.validator, "_vm_exists", return_value=False):
            result = self.validator.validate_stop_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.NOT_FOUND)
        self.assertEqual(result.code, ValidationErrorCode.VM_NOT_FOUND)

    def test_validate_stop_vm_already_stopped(self):
        """Test stop VM validation no-ops when VM already stopped."""
        with (
            patch.object(self.validator, "_vm_exists", return_value=True),
            patch.object(self.validator, "_get_vm_status", return_value="shut off"),
        ):
            result = self.validator.validate_stop_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertTrue(result.should_noop)
        self.assertEqual(result.status, ValidationStatus.NOOP_SUCCESS)
        self.assertEqual(result.code, ValidationErrorCode.VM_ALREADY_STOPPED)

    def test_validate_destroy_vm_success(self):
        """Test destroy VM validation succeeds when VM exists."""
        with (
            patch.object(self.validator, "_vm_exists", return_value=True),
            patch.object(self.validator, "_disk_exists", return_value=True),
        ):
            result = self.validator.validate_destroy_vm({"vmName": "test-vm"}, job_id=1)

        self.assertTrue(result.is_valid)

    def test_validate_destroy_vm_already_gone(self):
        """Test destroy VM validation no-ops when VM already gone."""
        with (
            patch.object(self.validator, "_vm_exists", return_value=False),
            patch.object(self.validator, "_disk_exists", return_value=False),
        ):
            result = self.validator.validate_destroy_vm({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertTrue(result.should_noop)
        self.assertEqual(result.status, ValidationStatus.NOOP_SUCCESS)
        self.assertEqual(result.code, ValidationErrorCode.VM_NOT_FOUND)

    def test_validate_clone_vm_success(self):
        """Test clone VM validation succeeds with valid source and target."""
        self.db_client.get_vm_definition_by_name.return_value = {
            "id": 2,
            "vm_name": "target-vm",
            "config": {},
        }

        with (
            patch.object(self.validator, "_vm_exists") as mock_vm_exists,
            patch.object(self.validator, "_disk_exists") as mock_disk_exists,
        ):
            # Source exists, target doesn't
            mock_vm_exists.side_effect = [True, False]
            mock_disk_exists.side_effect = [True, False]

            result = self.validator.validate_clone_vm(
                {"sourceVmName": "source-vm", "targetVmName": "target-vm"}, job_id=1
            )

        self.assertTrue(result.is_valid)

    def test_validate_clone_vm_source_not_found(self):
        """Test clone VM validation fails when source VM doesn't exist."""
        with patch.object(self.validator, "_vm_exists", return_value=False):
            result = self.validator.validate_clone_vm(
                {"sourceVmName": "source-vm", "targetVmName": "target-vm"}, job_id=1
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.NOT_FOUND)
        self.assertEqual(result.code, ValidationErrorCode.VM_NOT_FOUND)
        self.assertIn("Source VM", result.reason)

    def test_validate_clone_vm_target_already_exists(self):
        """Test clone VM validation fails when target VM already exists."""
        self.db_client.get_vm_definition_by_name.return_value = {
            "id": 2,
            "vm_name": "target-vm",
            "config": {},
        }

        with (
            patch.object(self.validator, "_vm_exists") as mock_vm_exists,
            patch.object(self.validator, "_disk_exists") as mock_disk_exists,
        ):
            # Source exists, target also exists
            mock_vm_exists.side_effect = [True, True]
            mock_disk_exists.side_effect = [True, True]

            result = self.validator.validate_clone_vm(
                {"sourceVmName": "source-vm", "targetVmName": "target-vm"}, job_id=1
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.ALREADY_EXISTS)
        self.assertEqual(result.code, ValidationErrorCode.VM_ALREADY_EXISTS)

    def test_validate_snapshot_create_success(self):
        """Test snapshot create validation succeeds when VM exists."""
        with patch.object(self.validator, "_vm_exists", return_value=True):
            result = self.validator.validate_snapshot_create({"vmName": "test-vm"}, job_id=1)

        self.assertTrue(result.is_valid)

    def test_validate_snapshot_create_vm_not_found(self):
        """Test snapshot create validation fails when VM doesn't exist."""
        with patch.object(self.validator, "_vm_exists", return_value=False):
            result = self.validator.validate_snapshot_create({"vmName": "test-vm"}, job_id=1)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.NOT_FOUND)
        self.assertEqual(result.code, ValidationErrorCode.VM_NOT_FOUND)

    def test_validate_snapshot_restore_success(self):
        """Test snapshot restore validation succeeds when VM and snapshot exist."""
        self.db_client.get_vm_snapshot.return_value = {
            "vm_name": "test-vm",
            "snapshot_id": "snap-1",
            "metadata": {},
        }

        with patch.object(self.validator, "_vm_exists", return_value=True):
            result = self.validator.validate_snapshot_restore(
                {"vmName": "test-vm", "snapshotId": "snap-1"}, job_id=1
            )

        self.assertTrue(result.is_valid)

    def test_validate_snapshot_restore_snapshot_not_found(self):
        """Test snapshot restore validation fails when snapshot doesn't exist."""
        self.db_client.get_vm_snapshot.return_value = None

        with patch.object(self.validator, "_vm_exists", return_value=True):
            result = self.validator.validate_snapshot_restore(
                {"vmName": "test-vm", "snapshotId": "snap-1"}, job_id=1
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.NOT_FOUND)
        self.assertEqual(result.code, ValidationErrorCode.SNAPSHOT_NOT_FOUND)

    def test_validate_snapshot_delete_success(self):
        """Test snapshot delete validation succeeds when snapshot exists."""
        self.db_client.get_vm_snapshot.return_value = {
            "vm_name": "test-vm",
            "snapshot_id": "snap-1",
            "metadata": {},
        }

        result = self.validator.validate_snapshot_delete(
            {"vmName": "test-vm", "snapshotId": "snap-1"}, job_id=1
        )

        self.assertTrue(result.is_valid)

    def test_validate_snapshot_delete_already_gone(self):
        """Test snapshot delete validation no-ops when snapshot already gone."""
        self.db_client.get_vm_snapshot.return_value = None

        result = self.validator.validate_snapshot_delete(
            {"vmName": "test-vm", "snapshotId": "snap-1"}, job_id=1
        )

        self.assertFalse(result.is_valid)
        self.assertTrue(result.should_noop)
        self.assertEqual(result.status, ValidationStatus.NOOP_SUCCESS)
        self.assertEqual(result.code, ValidationErrorCode.SNAPSHOT_NOT_FOUND)

    def test_validate_job_unsupported_type(self):
        """Test validation fails for unsupported job type."""
        job = {
            "id": 1,
            "type": "unsupported_operation",
            "targetHostId": "test-host-1",
            "payload": {},
        }

        result = self.validator.validate_job(job)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.INVALID_PAYLOAD)
        self.assertEqual(result.code, ValidationErrorCode.UNSUPPORTED_JOB_TYPE)

    def test_validate_job_full_workflow(self):
        """Test full validation workflow for provision_vm job."""
        self.db_client.get_vm_definition_by_name.return_value = {
            "id": 1,
            "vm_name": "test-vm",
            "config": {},
        }

        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "test-host-1",
            "payload": {"vmName": "test-vm"},
        }

        with (
            patch.object(self.validator, "_vm_exists", return_value=False),
            patch.object(self.validator, "_disk_exists", return_value=False),
        ):
            result = self.validator.validate_job(job)

        self.assertTrue(result.is_valid)
        self.assertTrue(result.should_execute)

    def test_validate_job_wrong_host_short_circuits(self):
        """Test validation short-circuits on wrong host."""
        job = {
            "id": 1,
            "type": "provision_vm",
            "targetHostId": "wrong-host",
            "payload": {"vmName": "test-vm"},
        }

        result = self.validator.validate_job(job)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, ValidationStatus.WRONG_HOST)
        # Should not have checked VM existence
        self.db_client.get_vm_definition_by_name.assert_not_called()


if __name__ == "__main__":
    unittest.main()
