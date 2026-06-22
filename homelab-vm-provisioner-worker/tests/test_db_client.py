"""Tests for worker database client."""

import json
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

from hlvmp_worker.db_client import DatabaseClient


class TestDatabaseClient(unittest.TestCase):
    """Test database client for job operations."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = DatabaseClient("http://localhost:3002", "test-password")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_health_check_success(self, mock_urlopen):
        """Test successful health check."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.health_check()

        self.assertTrue(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_health_check_failure(self, mock_urlopen):
        """Test health check when service is down."""
        mock_urlopen.side_effect = URLError("Connection refused")

        result = self.client.health_check()

        self.assertFalse(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_claim_next_job_success(self, mock_urlopen):
        """Test claiming a job successfully."""
        job_data = {
            "job": {
                "id": 1,
                "type": "provision_vm",
                "status": "queued",
                "targetHostId": "host1",
                "payload": {"configPath": "/path/to/config.yaml"},
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(job_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.claim_next_job("host1", "worker1")

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 1)
        self.assertEqual(result["type"], "provision_vm")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_claim_next_job_none_available(self, mock_urlopen):
        """Test claiming when no jobs are available."""
        mock_response = Mock()
        mock_response.code = 404
        mock_response.read.return_value = b'{"error": "No jobs available"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 404, "Not Found", {}, mock_response
        )

        result = self.client.claim_next_job("host1", "worker1")

        self.assertIsNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_mark_job_running(self, mock_urlopen):
        """Test marking a job as running."""
        job_data = {
            "job": {
                "id": 1,
                "type": "provision_vm",
                "status": "running",
                "targetHostId": "host1",
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(job_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.mark_job_running(1, "worker1")

        self.assertEqual(result["id"], 1)
        self.assertEqual(result["status"], "running")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/jobs/1/running"))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_mark_job_succeeded(self, mock_urlopen):
        """Test marking a job as succeeded."""
        job_data = {
            "job": {
                "id": 1,
                "type": "provision_vm",
                "status": "succeeded",
                "result": {"success": True},
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(job_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.mark_job_succeeded(1, {"success": True})

        self.assertEqual(result["id"], 1)
        self.assertEqual(result["status"], "succeeded")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/jobs/1/succeeded"))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_mark_job_failed(self, mock_urlopen):
        """Test marking a job as failed."""
        job_data = {
            "job": {
                "id": 1,
                "type": "provision_vm",
                "status": "failed",
                "error": "Test error",
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(job_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.mark_job_failed(1, "Test error", retriable=False)

        self.assertEqual(result["id"], 1)
        self.assertEqual(result["status"], "failed")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/jobs/1/failed"))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_append_job_event(self, mock_urlopen):
        """Test appending an event to a job."""
        event_data = {
            "event": {
                "id": 1,
                "jobId": 1,
                "level": "info",
                "message": "Test message",
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(event_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.append_job_event(1, "info", "Test message")

        self.assertEqual(result["jobId"], 1)
        self.assertEqual(result["level"], "info")
        self.assertEqual(result["message"], "Test message")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_acquire_resource_locks_success(self, mock_urlopen):
        """Test acquiring resource locks successfully."""
        lock_data = {"acquired": True}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(lock_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.acquire_resource_locks(1, "worker1", ["vm:test-vm"])

        self.assertTrue(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_acquire_resource_locks_conflict(self, mock_urlopen):
        """Test acquiring resource locks when already locked."""
        mock_response = Mock()
        mock_response.code = 409
        mock_response.read.return_value = b'{"error": "Resource locked"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 409, "Conflict", {}, mock_response
        )

        result = self.client.acquire_resource_locks(1, "worker1", ["vm:test-vm"])

        self.assertFalse(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_release_resource_locks(self, mock_urlopen):
        """Test releasing resource locks."""
        lock_data = {"released": 2}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(lock_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.release_resource_locks(1, "worker1")

        self.assertEqual(result, 2)
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/locks/release"))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_request_with_http_error(self, mock_urlopen):
        """Test request handling when HTTP error occurs."""
        mock_response = Mock()
        mock_response.code = 500
        mock_response.read.return_value = b'{"error": "Internal server error"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 500, "Internal Server Error", {}, mock_response
        )

        with self.assertRaises(RuntimeError) as context:
            self.client.mark_job_running(1, "worker1")

        self.assertIn("Status 500", str(context.exception))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_request_with_url_error(self, mock_urlopen):
        """Test request handling when connection fails."""
        mock_urlopen.side_effect = URLError("Connection refused")

        with self.assertRaises(RuntimeError) as context:
            self.client.mark_job_running(1, "worker1")

        self.assertIn("Failed to connect", str(context.exception))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_upsert_vm_runtime_state(self, mock_urlopen):
        """Test upserting VM runtime state."""
        state_data = {
            "runtimeState": {
                "status": "running",
                "mac_address": "52:54:00:11:22:33",
                "ip_address": "10.80.0.2",
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(state_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.upsert_vm_runtime_state(
            "test-vm", {"status": "running"}, "worker"
        )

        self.assertEqual(result["status"], "running")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_delete_vm_runtime_state(self, mock_urlopen):
        """Test deleting VM runtime state."""
        state_data = {"runtimeState": {"status": "destroyed"}}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(state_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.delete_vm_runtime_state("test-vm")

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "destroyed")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_delete_vm_runtime_state_not_found(self, mock_urlopen):
        """Test deleting VM runtime state when not found."""
        mock_response = Mock()
        mock_response.code = 404
        mock_response.read.return_value = b'{"error": "Not found"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 404, "Not Found", {}, mock_response
        )

        result = self.client.delete_vm_runtime_state("test-vm")

        self.assertIsNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_list_vm_snapshots(self, mock_urlopen):
        """Test listing VM snapshots."""
        snapshot_data = {
            "snapshots": [
                {"id": "snap1", "createdAt": "2024-01-01T00:00:00Z"},
                {"id": "snap2", "createdAt": "2024-01-02T00:00:00Z"},
            ]
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(snapshot_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.list_vm_snapshots("test-vm")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "snap1")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_snapshot(self, mock_urlopen):
        """Test getting a VM snapshot."""
        snapshot_data = {
            "snapshot": {"id": "snap1", "createdAt": "2024-01-01T00:00:00Z"}
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(snapshot_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.get_vm_snapshot("test-vm", "snap1")

        self.assertEqual(result["id"], "snap1")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_snapshot_not_found(self, mock_urlopen):
        """Test getting VM snapshot when not found."""
        mock_response = Mock()
        mock_response.code = 404
        mock_response.read.return_value = b'{"error": "Not found"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 404, "Not Found", {}, mock_response
        )

        result = self.client.get_vm_snapshot("test-vm", "snap1")

        self.assertIsNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_upsert_vm_snapshot(self, mock_urlopen):
        """Test upserting VM snapshot."""
        snapshot_data = {
            "snapshot": {"id": "snap1", "metadata": {"description": "test"}}
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(snapshot_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.upsert_vm_snapshot(
            "test-vm", "snap1", {"description": "test"}
        )

        self.assertEqual(result["id"], "snap1")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_delete_vm_snapshot(self, mock_urlopen):
        """Test deleting VM snapshot."""
        snapshot_data = {"snapshot": {"id": "snap1"}}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(snapshot_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.delete_vm_snapshot("test-vm", "snap1")

        self.assertIsNotNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_store_vm_log_snapshot(self, mock_urlopen):
        """Test storing VM log snapshot."""
        log_data = {"logSnapshot": {"vmName": "test-vm", "lineCount": 100}}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(log_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.store_vm_log_snapshot("test-vm", "log content", 100)

        self.assertEqual(result["logSnapshot"]["vmName"], "test-vm")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_log_snapshot(self, mock_urlopen):
        """Test getting VM log snapshot."""
        log_data = {"logSnapshot": {"vmName": "test-vm"}}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(log_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.get_vm_log_snapshot("test-vm")

        self.assertIsNotNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_log_snapshot_not_found(self, mock_urlopen):
        """Test getting VM log snapshot when not found."""
        mock_response = Mock()
        mock_response.code = 404
        mock_response.read.return_value = b'{"error": "Not found"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 404, "Not Found", {}, mock_response
        )

        result = self.client.get_vm_log_snapshot("test-vm")

        self.assertIsNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_list_vm_log_snapshots(self, mock_urlopen):
        """Test listing all VM log snapshots."""
        log_data = [{"vmName": "test-vm1"}, {"vmName": "test-vm2"}]

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(log_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.list_vm_log_snapshots()

        self.assertEqual(len(result), 2)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_delete_vm_log_snapshot(self, mock_urlopen):
        """Test deleting VM log snapshot."""
        log_data = {"deleted": {"vmName": "test-vm"}}

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(log_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.delete_vm_log_snapshot("test-vm")

        self.assertIsNotNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_request_with_empty_response(self, mock_urlopen):
        """Test request handling with empty response body."""
        mock_response = MagicMock()
        mock_response.read.return_value = b""
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.health_check()

        self.assertFalse(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_request_with_invalid_json_error(self, mock_urlopen):
        """Test request handling when error body is not valid JSON."""
        mock_response = Mock()
        mock_response.code = 500
        mock_response.read.return_value = b"Plain text error message"

        mock_urlopen.side_effect = HTTPError(
            "http://test", 500, "Internal Server Error", {}, mock_response
        )

        with self.assertRaises(RuntimeError) as context:
            self.client.mark_job_running(1, "worker1")

        self.assertIn("Plain text error message", str(context.exception))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_request_with_generic_exception(self, mock_urlopen):
        """Test request handling with unexpected exception."""
        mock_urlopen.side_effect = Exception("Unexpected error")

        with self.assertRaises(RuntimeError) as context:
            self.client.claim_next_job("host1", "worker1")

        self.assertIn("Database request error", str(context.exception))

    @patch("hlvmp_worker.db_client.urlopen")
    def test_list_jobs_no_filters(self, mock_urlopen):
        """Test listing jobs without filters."""
        jobs_data = {
            "jobs": [
                {"id": 1, "type": "provision_vm", "status": "queued"},
                {"id": 2, "type": "delete_vm", "status": "running"},
            ]
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(jobs_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.list_jobs()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[1]["id"], 2)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_list_jobs_with_filters(self, mock_urlopen):
        """Test listing jobs with status and host filters."""
        jobs_data = {
            "jobs": [{"id": 1, "type": "provision_vm", "status": "running"}]
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(jobs_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.list_jobs(status="running", target_host_id="host1", limit=50)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "running")
        request = mock_urlopen.call_args.args[0]
        self.assertIn("status=running", request.full_url)
        self.assertIn("targetHostId=host1", request.full_url)
        self.assertIn("limit=50", request.full_url)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_definition_by_name_success(self, mock_urlopen):
        """Test fetching VM definition by name."""
        vm_def_data = {
            "vmDefinition": {
                "name": "test-vm",
                "memory": 2048,
                "cpus": 2,
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(vm_def_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.get_vm_definition_by_name("test-vm")

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "test-vm")
        self.assertEqual(result["memory"], 2048)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_definition_by_name_not_found(self, mock_urlopen):
        """Test fetching VM definition when not found."""
        mock_response = Mock()
        mock_response.code = 404
        mock_response.read.return_value = b'{"error": "VM definition not found"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 404, "Not Found", {}, mock_response
        )

        result = self.client.get_vm_definition_by_name("missing-vm")

        self.assertIsNone(result)

    @patch("hlvmp_worker.db_client.urlopen")
    def test_list_vm_definitions(self, mock_urlopen):
        """Test listing all VM definitions."""
        vm_defs_data = {
            "vmDefinitions": [
                {"name": "vm1", "memory": 2048},
                {"name": "vm2", "memory": 4096},
            ]
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(vm_defs_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.list_vm_definitions()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "vm1")
        self.assertEqual(result[1]["name"], "vm2")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_list_network_groups(self, mock_urlopen):
        """Test listing all network groups."""
        groups_data = {
            "networkGroups": [
                {"name": "group1", "subnet": "10.80.0.0/24"},
                {"name": "group2", "subnet": "10.81.0.0/24"},
            ]
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(groups_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.list_network_groups()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "group1")
        self.assertEqual(result[1]["name"], "group2")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_list_vm_runtime_states(self, mock_urlopen):
        """Test listing all VM runtime states."""
        states_data = {
            "runtimeStates": [
                {"vm_name": "vm1", "status": "running"},
                {"vm_name": "vm2", "status": "stopped"},
            ]
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(states_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.list_vm_runtime_states()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["vm_name"], "vm1")
        self.assertEqual(result[1]["vm_name"], "vm2")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_runtime_state_success(self, mock_urlopen):
        """Test fetching VM runtime state."""
        state_data = {
            "runtimeState": {
                "vm_name": "test-vm",
                "status": "running",
                "ip_address": "10.80.0.2",
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(state_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.get_vm_runtime_state("test-vm")

        self.assertIsNotNone(result)
        self.assertEqual(result["vm_name"], "test-vm")
        self.assertEqual(result["status"], "running")

    @patch("hlvmp_worker.db_client.urlopen")
    def test_get_vm_runtime_state_not_found(self, mock_urlopen):
        """Test fetching VM runtime state when not found."""
        mock_response = Mock()
        mock_response.code = 404
        mock_response.read.return_value = b'{"error": "Runtime state not found"}'

        mock_urlopen.side_effect = HTTPError(
            "http://test", 404, "Not Found", {}, mock_response
        )

        result = self.client.get_vm_runtime_state("missing-vm")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
