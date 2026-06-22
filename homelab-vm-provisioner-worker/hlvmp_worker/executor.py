"""Job executor for provisioner operations.

Maps job types to provisioner Python service functions and executes them
in-process on the host.
"""

import sys
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from .db_client import DatabaseClient


class JobExecutionError(Exception):
    """Raised when job execution fails."""

    def __init__(self, message: str, retriable: bool = False):
        """Initialize job execution error.

        Args:
            message: Error message
            retriable: Whether the job can be retried
        """
        super().__init__(message)
        self.retriable = retriable


class JobExecutor:
    """Executes provisioner jobs by calling provisioner Python services."""

    def __init__(self, provisioner_cli_path: str, db_client: DatabaseClient):
        """Initialize job executor.

        Args:
            provisioner_cli_path: Path to provisioner CLI directory
        """
        self.provisioner_cli_path = Path(provisioner_cli_path)
        self.db_client = db_client
        self.service_mode = self._load_service_mode_module()

        self._handlers: dict[str, Callable] = {
            "provision_vm": self._execute_provision_vm,
            "destroy_vm": self._execute_destroy_vm,
            "clone_vm": self._execute_clone_vm,
            "start_vm": self._execute_start_vm,
            "stop_vm": self._execute_stop_vm,
            "reconcile_vm_networking": self._execute_reconcile_networking,
            "refresh_vm_runtime_state": self._execute_refresh_runtime_state,
            "snapshot_create": self._execute_snapshot_create,
            "snapshot_restore": self._execute_snapshot_restore,
            "snapshot_delete": self._execute_snapshot_delete,
            "collect_vm_logs": self._execute_collect_vm_logs,
        }

    def _load_service_mode_module(self):
        if not self.provisioner_cli_path.exists():
            raise ValueError(f"Provisioner CLI path does not exist: {self.provisioner_cli_path}")

        try:
            module_root = str(self.provisioner_cli_path.resolve())
            if module_root not in sys.path:
                sys.path.insert(0, module_root)
            return import_module("homelab_vm_provisioner.service_mode")
        except ModuleNotFoundError as e:
            raise JobExecutionError(
                f"Could not import provisioner service module: {e}",
                retriable=False
            ) from e
        except Exception as e:
            raise JobExecutionError(
                f"Failed to initialize provisioner service module: {e}",
                retriable=True
            ) from e

    def _load_vm_definition(self, vm_name: str) -> dict[str, Any]:
        vm_definition = self.db_client.get_vm_definition_by_name(vm_name)
        if not vm_definition:
            raise JobExecutionError(
                f"VM definition not found: {vm_name}", retriable=False
            )

        return vm_definition

    def _build_service_config(self, vm_definition: dict[str, Any]) -> dict[str, Any]:
        config_data = dict(vm_definition.get("config") or {})
        vm_config = dict(config_data.get("vm") or {})
        scripts_config = dict(config_data.get("scripts") or {})

        if vm_definition.get("ssh_public_key"):
            vm_config["ssh_public_key"] = vm_definition["ssh_public_key"].rstrip("\n")
        if vm_definition.get("setup_script"):
            scripts_config["setup_script_content"] = vm_definition["setup_script"]

        config_data["vm"] = vm_config
        if scripts_config:
            config_data["scripts"] = scripts_config

        return config_data

    def _build_reconcile_payload(self, policy_only: bool) -> dict[str, Any]:
        vm_definitions = self.db_client.list_vm_definitions()
        runtime_states = {
            row["vm_name"]: row["state"]
            for row in self.db_client.list_vm_runtime_states()
        }
        network_groups = self.db_client.list_network_groups()
        vm_records = []
        for vm_definition in vm_definitions:
            config_data = vm_definition.get("config") or {}
            vm_config = config_data.get("vm") or {}
            network = config_data.get("network") or {}
            runtime_state = runtime_states.get(vm_definition["vm_name"], {}) or {}
            effective_network = runtime_state.get("network") or network
            vm_records.append(
                {
                    "vm_name": vm_definition["vm_name"],
                    "owner_user_id": vm_config.get("owner_user_id"),
                    "network_group_id": vm_config.get("network_group_id") or effective_network.get("network_group_id"),
                    "network_group_name": effective_network.get("group_name") or vm_definition["vm_name"],
                    "profile": effective_network.get("profile") or effective_network.get("mode"),
                    "libvirt_network_name": effective_network.get("libvirt_network_name") or effective_network.get("name"),
                    "bridge_name": effective_network.get("bridge_name"),
                    "subnet_cidr": effective_network.get("subnet_cidr") or effective_network.get("cidr"),
                    "gateway_ip": effective_network.get("gateway_ip") or effective_network.get("gateway"),
                    "dhcp_start": effective_network.get("dhcp_start"),
                    "dhcp_end": effective_network.get("dhcp_end"),
                    "mac_address": runtime_state.get("mac_address") or vm_config.get("mac_address") or effective_network.get("mac"),
                    "ip_address": runtime_state.get("ip_address") or vm_config.get("ip_address") or effective_network.get("vm_ip"),
                    "allow_same_group_traffic": vm_config.get("allow_same_group_traffic", True),
                    "allow_host_access": vm_config.get("allow_host_access", True),
                    "allow_private_lan_access": bool(vm_config.get("allow_private_lan_access", False)),
                    "internet_access": vm_config.get("internet_access", True),
                    "ports": runtime_state.get("ports") or config_data.get("ports") or [],
                    "state_exists": True,
                }
            )

        return {
            "policy_only": policy_only,
            "network_groups": network_groups,
            "vm_records": vm_records,
        }

    def refresh_vm_runtime_state_cache(self, vm_name: str) -> dict[str, Any] | None:
        """Refresh cached runtime state for one VM from live observation."""
        vm_definition = self.db_client.get_vm_definition_by_name(vm_name)
        if not vm_definition:
            return None

        return self.service_mode.refresh_vm_runtime_state(vm_name)

    def refresh_all_runtime_state_caches(self) -> list[dict[str, Any]]:
        """Refresh cached runtime state for all known VM definitions."""
        refreshed_states = []
        for vm_definition in self.db_client.list_vm_definitions():
            try:
                refreshed_state = self.service_mode.refresh_vm_runtime_state(vm_definition["vm_name"])
                refreshed_states.append({
                    "vmName": vm_definition["vm_name"],
                    "runtimeState": refreshed_state,
                })
            except Exception:
                continue

        return refreshed_states

    def get_supported_job_types(self) -> list[str]:
        """Get list of supported job types.

        Returns:
            List of job type strings
        """
        return list(self._handlers.keys())

    def get_resource_locks(self, job: dict[str, Any]) -> list[str]:
        """Determine resource locks required for a job.

        Args:
            job: Job data dictionary

        Returns:
            List of lock keys required for this job
        """
        job_type = job["type"]
        target_host_id = job["targetHostId"]
        target_vm_id = job.get("targetVmId")

        locks = []

        # VM-specific mutation jobs lock the VM
        if job_type in (
            "provision_vm",
            "destroy_vm",
            "clone_vm",
            "start_vm",
            "stop_vm",
            "snapshot_create",
            "snapshot_restore",
            "snapshot_delete",
        ):
            if target_vm_id:
                locks.append(f"vm:{target_vm_id}")
            else:
                # Fallback to host lock if VM ID is missing
                locks.append(f"host:{target_host_id}")

        # Network reconciliation locks firewall and network
        elif job_type == "reconcile_vm_networking":
            locks.append(f"firewall:{target_host_id}")
            locks.append(f"network:{target_host_id}")

        # Default to host lock for unknown job types
        else:
            locks.append(f"host:{target_host_id}")

        # Sort locks for deterministic ordering (prevent deadlocks)
        return sorted(locks)

    def execute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        """Execute a job based on its type.

        Args:
            job: Job data dictionary with type, payload, etc.

        Returns:
            Job result data

        Raises:
            JobExecutionError: If job execution fails
        """
        job_type = job["type"]
        payload = job.get("payload", {})

        handler = self._handlers.get(job_type)
        if not handler:
            raise JobExecutionError(
                f"Unsupported job type: {job_type}", retriable=False
            )

        try:
            return handler(payload)
        except JobExecutionError:
            raise
        except Exception as e:
            raise JobExecutionError(
                f"Job execution failed: {e}", retriable=True
            ) from e

    def _execute_provision_vm(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute VM provision job.

        Args:
            payload: Job payload with vmName

        Returns:
            Job result data

        Raises:
            JobExecutionError: If provision fails
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )

        vm_definition = self._load_vm_definition(vm_name)
        reconcile_payload = self._build_reconcile_payload(False)
        self.service_mode.create_vm(
            self._build_service_config(vm_definition),
            reconcile_payload=reconcile_payload,
        )
        runtime_state = self.service_mode.refresh_vm_runtime_state(vm_name)
        return {
            "success": True,
            "vmName": vm_name,
            "vmDefinitionId": vm_definition["id"],
            "runtimeState": runtime_state,
            "message": "VM provisioned successfully",
        }

    def _execute_destroy_vm(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute VM destroy job.

        Args:
            payload: Job payload with vmName

        Returns:
            Job result data

        Raises:
            JobExecutionError: If destroy fails
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )

        self.service_mode.destroy_vm(vm_name)
        return {
            "success": True,
            "vmName": vm_name,
            "deleteRuntimeState": True,
            "message": "VM destroyed successfully",
        }

    def _execute_clone_vm(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute VM clone job.

        Args:
            payload: Job payload with sourceVmName and targetVmName

        Returns:
            Job result data

        Raises:
            JobExecutionError: If clone fails
        """
        source_vm_name = payload.get("sourceVmName")
        target_vm_name = payload.get("targetVmName")

        if not source_vm_name:
            raise JobExecutionError(
                "Missing required field: sourceVmName", retriable=False
            )
        if not target_vm_name:
            raise JobExecutionError(
                "Missing required field: targetVmName", retriable=False
            )

        vm_definition = self._load_vm_definition(target_vm_name)
        reconcile_payload = self._build_reconcile_payload(False)
        self.service_mode.clone_vm(
            source_vm_name,
            self._build_service_config(vm_definition),
            reconcile_payload=reconcile_payload,
        )
        runtime_state = self.service_mode.refresh_vm_runtime_state(target_vm_name)
        return {
            "success": True,
            "sourceVmName": source_vm_name,
            "targetVmName": target_vm_name,
            "vmDefinitionId": vm_definition["id"],
            "runtimeState": runtime_state,
            "message": "VM cloned successfully",
        }

    def _execute_start_vm(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute VM start job.

        Args:
            payload: Job payload with vmName

        Returns:
            Job result data

        Raises:
            JobExecutionError: If start fails
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )

        self.service_mode.start_vm(vm_name)
        runtime_state = self.service_mode.refresh_vm_runtime_state(vm_name)
        return {
            "success": True,
            "vmName": vm_name,
            "runtimeState": runtime_state,
            "message": "VM started successfully",
        }

    def _execute_stop_vm(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute VM stop job.

        Args:
            payload: Job payload with vmName

        Returns:
            Job result data

        Raises:
            JobExecutionError: If stop fails
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )

        self.service_mode.stop_vm(vm_name)
        runtime_state = self.service_mode.refresh_vm_runtime_state(vm_name)
        return {
            "success": True,
            "vmName": vm_name,
            "runtimeState": runtime_state,
            "message": "VM stopped successfully",
        }

    def _execute_reconcile_networking(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute network reconciliation job.

        Args:
            payload: Job payload with optional policyOnly flag

        Returns:
            Job result data

        Raises:
            JobExecutionError: If reconciliation fails
        """
        policy_only = payload.get("policyOnly", False)

        reconcile_payload = self._build_reconcile_payload(policy_only)

        self.service_mode.reconcile_vm_records(
            reconcile_payload["vm_records"],
            network_groups=reconcile_payload.get("network_groups"),
            policy_only=policy_only,
        )
        return {
            "success": True,
            "policyOnly": policy_only,
            "message": "Network reconciliation completed successfully",
        }

    def _execute_refresh_runtime_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute runtime state refresh job.

        Args:
            payload: Job payload with vmName

        Returns:
            Job result data with refreshed runtime state

        Raises:
            JobExecutionError: If refresh fails
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )

        runtime_state = self.service_mode.refresh_vm_runtime_state(vm_name)

        return {
            "success": True,
            "vmName": vm_name,
            "runtimeState": runtime_state,
            "observationSource": "explicit_refresh",
            "message": "Runtime state refreshed successfully",
        }

    def _execute_snapshot_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute snapshot creation job.

        Args:
            payload: Job payload with vmName

        Returns:
            Job result data

        Raises:
            JobExecutionError: If snapshot creation fails
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )

        vm_definition = self._load_vm_definition(vm_name)
        runtime_state = self.db_client.get_vm_runtime_state(vm_name)
        snapshot_record = self.service_mode.create_snapshot_record(
            vm_name,
            {
                "config_snapshot": self._build_service_config(vm_definition),
                "runtime_state_snapshot": (runtime_state or {}).get("state", {}),
            },
        )
        return {
            "success": True,
            "vmName": vm_name,
            "snapshotId": snapshot_record.get("snapshot_id"),
            "snapshotRecord": snapshot_record,
            "message": "Snapshot created successfully",
        }

    def _execute_snapshot_restore(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute snapshot restore job.

        Args:
            payload: Job payload with vmName and snapshotId

        Returns:
            Job result data

        Raises:
            JobExecutionError: If snapshot restore fails
        """
        vm_name = payload.get("vmName")
        snapshot_id = payload.get("snapshotId")

        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )
        if not snapshot_id:
            raise JobExecutionError(
                "Missing required field: snapshotId", retriable=False
            )

        snapshot_record = self.db_client.get_vm_snapshot(vm_name, snapshot_id)
        if not snapshot_record:
            raise JobExecutionError(
                f"Snapshot not found: {vm_name}/{snapshot_id}", retriable=False
            )

        reconcile_payload = self._build_reconcile_payload(False)
        self.service_mode.restore_snapshot_record(
            vm_name,
            snapshot_id,
            snapshot_record.get("metadata") or snapshot_record,
            vm_records=reconcile_payload.get("vm_records"),
            network_groups=reconcile_payload.get("network_groups"),
        )
        runtime_state = self.service_mode.refresh_vm_runtime_state(vm_name)
        return {
            "success": True,
            "vmName": vm_name,
            "snapshotId": snapshot_id,
            "runtimeState": runtime_state,
            "message": "Snapshot restored successfully",
        }

    def _execute_snapshot_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute snapshot deletion job.

        Args:
            payload: Job payload with vmName and snapshotId

        Returns:
            Job result data

        Raises:
            JobExecutionError: If snapshot deletion fails
        """
        vm_name = payload.get("vmName")
        snapshot_id = payload.get("snapshotId")

        if not vm_name:
            raise JobExecutionError(
                "Missing required field: vmName", retriable=False
            )
        if not snapshot_id:
            raise JobExecutionError(
                "Missing required field: snapshotId", retriable=False
            )

        snapshot_record = self.db_client.get_vm_snapshot(vm_name, snapshot_id)
        if not snapshot_record:
            raise JobExecutionError(
                f"Snapshot not found: {vm_name}/{snapshot_id}", retriable=False
            )

        self.service_mode.delete_snapshot_record(
            vm_name,
            snapshot_id,
            snapshot_record.get("metadata") or snapshot_record,
        )
        return {
            "success": True,
            "vmName": vm_name,
            "snapshotId": snapshot_id,
            "deleteSnapshotRecord": True,
            "message": "Snapshot deleted successfully",
        }

    def _execute_collect_vm_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Collect VM logs from libvirt and store in database.

        Enforces 1MB size limit per VM log snapshot.

        Args:
            payload: Job payload with vmName and optional lines count

        Returns:
            Job result data

        Raises:
            JobExecutionError: If log collection fails
        """
        vm_name = payload.get("vmName")
        lines = payload.get("lines", 500)
        max_size_bytes = 1024 * 1024  # 1MB limit

        if not vm_name:
            raise JobExecutionError("Missing required field: vmName", retriable=False)

        # Read logs from libvirt
        log_path = Path(f"/var/log/libvirt/qemu/{vm_name}.log")

        if not log_path.exists():
            # No log file - VM might not exist or hasn't been started
            return {
                "vm_name": vm_name,
                "lines_collected": 0,
                "log_exists": False,
                "observation_source": "worker_log_collection",
            }

        try:
            # Read last N lines efficiently while respecting size limit
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                log_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

            # Enforce 1MB size limit by truncating from the beginning if needed
            log_content = "".join(log_lines)
            content_size = len(log_content.encode("utf-8"))

            if content_size > max_size_bytes:
                # Truncate to fit within 1MB, removing lines from the beginning
                truncated_lines = []
                current_size = 0

                for line in reversed(log_lines):
                    line_size = len(line.encode("utf-8"))
                    if current_size + line_size > max_size_bytes:
                        break
                    truncated_lines.insert(0, line)
                    current_size += line_size

                log_lines = truncated_lines
                log_content = "".join(log_lines)

            line_count = len(log_lines)

            # Store in database
            self.db_client.store_vm_log_snapshot(
                vm_name=vm_name,
                log_content=log_content,
                line_count=line_count,
                collected_by="worker",
            )

            return {
                "vm_name": vm_name,
                "lines_collected": line_count,
                "log_exists": True,
                "size_bytes": len(log_content.encode("utf-8")),
                "observation_source": "worker_log_collection",
            }
        except PermissionError as e:
            raise JobExecutionError(
                f"Permission denied reading log file: {log_path}. Worker needs sudo or file read access.",
                retriable=True,
            ) from e
        except Exception as e:
            raise JobExecutionError(
                f"Failed to collect logs for {vm_name}: {e}",
                retriable=True,
            ) from e
