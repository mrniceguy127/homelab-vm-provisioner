"""Worker-level validation for job execution.

Validates jobs before execution to prevent unsafe duplicate or conflicting
operations and ensure idempotent job handling.
"""

from enum import Enum
from pathlib import Path
from typing import Any


class ValidationStatus(str, Enum):
    """Validation result status codes."""

    VALID = "valid"  # Job is safe to execute
    NOOP_SUCCESS = "noop_success"  # Job already completed, safe no-op
    INVALID_PAYLOAD = "invalid_payload"  # Payload schema validation failed
    WRONG_HOST = "wrong_host"  # Job not intended for this worker
    CONFLICT = "conflict"  # Conflicting state prevents execution
    ALREADY_EXISTS = "already_exists"  # Resource already exists
    NOT_FOUND = "not_found"  # Required resource not found
    UNSAFE_TO_RETRY = "unsafe_to_retry"  # Redelivery unsafe due to partial mutation
    CLEANUP_REQUIRED = "cleanup_required"  # Ambiguous state requires cleanup


class ValidationErrorCode(str, Enum):
    """Machine-readable validation error codes."""

    # Host validation
    HOST_MISMATCH = "HOST_MISMATCH"

    # Payload validation
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_FIELD_VALUE = "INVALID_FIELD_VALUE"
    UNSUPPORTED_JOB_TYPE = "UNSUPPORTED_JOB_TYPE"

    # VM validation
    VM_ALREADY_EXISTS = "VM_ALREADY_EXISTS"
    VM_NOT_FOUND = "VM_NOT_FOUND"
    VM_ALREADY_RUNNING = "VM_ALREADY_RUNNING"
    VM_ALREADY_STOPPED = "VM_ALREADY_STOPPED"
    VM_DEFINITION_NOT_FOUND = "VM_DEFINITION_NOT_FOUND"

    # Disk validation
    DISK_ALREADY_EXISTS = "DISK_ALREADY_EXISTS"
    DISK_NOT_FOUND = "DISK_NOT_FOUND"

    # Snapshot validation
    SNAPSHOT_ALREADY_EXISTS = "SNAPSHOT_ALREADY_EXISTS"
    SNAPSHOT_NOT_FOUND = "SNAPSHOT_NOT_FOUND"

    # State validation
    PARTIAL_STATE_DETECTED = "PARTIAL_STATE_DETECTED"
    STATE_MISMATCH = "STATE_MISMATCH"


class WorkerAction(str, Enum):
    """Recommended worker action based on validation result."""

    PROCEED = "proceed"  # Execute the job
    NOOP_SUCCESS = "noop_success"  # Report success without mutation
    FAIL = "fail"  # Report failure without mutation
    CLEANUP_REQUIRED = "cleanup_required"  # Report cleanup needed


class ValidationResult:
    """Result of worker-level job validation."""

    def __init__(
        self,
        status: ValidationStatus,
        action: WorkerAction,
        code: ValidationErrorCode | None = None,
        reason: str | None = None,
        job_id: int | None = None,
        target: str | None = None,
        observed_state: dict[str, Any] | None = None,
    ):
        """Initialize validation result.

        Args:
            status: Validation status
            action: Recommended worker action
            code: Machine-readable error code (required for non-valid status)
            reason: Human-readable explanation (required for non-valid status)
            job_id: Job ID being validated
            target: Target resource identifier (VM name, snapshot ID, etc.)
            observed_state: Observed host state relevant to validation
        """
        self.status = status
        self.action = action
        self.code = code
        self.reason = reason
        self.job_id = job_id
        self.target = target
        self.observed_state = observed_state or {}

    @property
    def is_valid(self) -> bool:
        """Return whether validation passed."""
        return self.status == ValidationStatus.VALID

    @property
    def should_execute(self) -> bool:
        """Return whether job should execute."""
        return self.action == WorkerAction.PROCEED

    @property
    def should_noop(self) -> bool:
        """Return whether job should no-op with success."""
        return self.action == WorkerAction.NOOP_SUCCESS

    @property
    def should_fail(self) -> bool:
        """Return whether job should fail without execution."""
        return self.action == WorkerAction.FAIL

    @property
    def requires_cleanup(self) -> bool:
        """Return whether job requires cleanup."""
        return self.action == WorkerAction.CLEANUP_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "status": self.status.value,
            "action": self.action.value,
            "code": self.code.value if self.code else None,
            "reason": self.reason,
            "job_id": self.job_id,
            "target": self.target,
            "observed_state": self.observed_state,
        }

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"ValidationResult(status={self.status.value}, "
            f"action={self.action.value}, code={self.code}, "
            f"reason={self.reason!r})"
        )

    @classmethod
    def valid(cls, job_id: int | None = None, target: str | None = None) -> "ValidationResult":
        """Create valid result."""
        return cls(
            status=ValidationStatus.VALID,
            action=WorkerAction.PROCEED,
            job_id=job_id,
            target=target,
        )

    @classmethod
    def noop_success(
        cls,
        code: ValidationErrorCode,
        reason: str,
        job_id: int | None = None,
        target: str | None = None,
        observed_state: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        """Create no-op success result."""
        return cls(
            status=ValidationStatus.NOOP_SUCCESS,
            action=WorkerAction.NOOP_SUCCESS,
            code=code,
            reason=reason,
            job_id=job_id,
            target=target,
            observed_state=observed_state,
        )

    @classmethod
    def invalid_payload(
        cls,
        code: ValidationErrorCode,
        reason: str,
        job_id: int | None = None,
        target: str | None = None,
    ) -> "ValidationResult":
        """Create invalid payload result."""
        return cls(
            status=ValidationStatus.INVALID_PAYLOAD,
            action=WorkerAction.FAIL,
            code=code,
            reason=reason,
            job_id=job_id,
            target=target,
        )

    @classmethod
    def wrong_host(
        cls,
        reason: str,
        job_id: int | None = None,
        target: str | None = None,
        observed_state: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        """Create wrong host result."""
        return cls(
            status=ValidationStatus.WRONG_HOST,
            action=WorkerAction.FAIL,
            code=ValidationErrorCode.HOST_MISMATCH,
            reason=reason,
            job_id=job_id,
            target=target,
            observed_state=observed_state,
        )

    @classmethod
    def conflict(
        cls,
        code: ValidationErrorCode,
        reason: str,
        job_id: int | None = None,
        target: str | None = None,
        observed_state: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        """Create conflict result."""
        return cls(
            status=ValidationStatus.CONFLICT,
            action=WorkerAction.FAIL,
            code=code,
            reason=reason,
            job_id=job_id,
            target=target,
            observed_state=observed_state,
        )

    @classmethod
    def already_exists(
        cls,
        code: ValidationErrorCode,
        reason: str,
        job_id: int | None = None,
        target: str | None = None,
        observed_state: dict[str, Any] | None = None,
        as_noop: bool = False,
    ) -> "ValidationResult":
        """Create already exists result."""
        return cls(
            status=ValidationStatus.ALREADY_EXISTS if not as_noop else ValidationStatus.NOOP_SUCCESS,
            action=WorkerAction.NOOP_SUCCESS if as_noop else WorkerAction.FAIL,
            code=code,
            reason=reason,
            job_id=job_id,
            target=target,
            observed_state=observed_state,
        )

    @classmethod
    def not_found(
        cls,
        code: ValidationErrorCode,
        reason: str,
        job_id: int | None = None,
        target: str | None = None,
        as_noop: bool = False,
    ) -> "ValidationResult":
        """Create not found result."""
        return cls(
            status=ValidationStatus.NOT_FOUND if not as_noop else ValidationStatus.NOOP_SUCCESS,
            action=WorkerAction.NOOP_SUCCESS if as_noop else WorkerAction.FAIL,
            code=code,
            reason=reason,
            job_id=job_id,
            target=target,
        )

    @classmethod
    def cleanup_required(
        cls,
        code: ValidationErrorCode,
        reason: str,
        job_id: int | None = None,
        target: str | None = None,
        observed_state: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        """Create cleanup required result."""
        return cls(
            status=ValidationStatus.CLEANUP_REQUIRED,
            action=WorkerAction.CLEANUP_REQUIRED,
            code=code,
            reason=reason,
            job_id=job_id,
            target=target,
            observed_state=observed_state,
        )


class JobValidator:
    """Validates jobs before execution on the worker."""

    def __init__(self, worker_config, db_client, service_mode):
        """Initialize job validator.

        Args:
            worker_config: Worker configuration with host_id
            db_client: Database client for state queries
            service_mode: Provisioner service mode module for host checks
        """
        self.worker_config = worker_config
        self.db_client = db_client
        self.service_mode = service_mode

    def validate_job(self, job: dict[str, Any]) -> ValidationResult:
        """Validate a job before execution.

        Args:
            job: Job data dictionary

        Returns:
            ValidationResult indicating whether job should execute
        """
        job_id = job.get("id")
        job_type = job["type"]
        payload = job.get("payload", {})

        # First validate host targeting
        host_result = self.validate_target_host(job)
        if not host_result.is_valid:
            return host_result

        # Then validate payload structure
        payload_result = self.validate_job_payload(job)
        if not payload_result.is_valid:
            return payload_result

        # Finally validate job-specific constraints
        if job_type == "provision_vm":
            return self.validate_provision_vm(payload, job_id)
        if job_type == "destroy_vm":
            return self.validate_destroy_vm(payload, job_id)
        if job_type == "clone_vm":
            return self.validate_clone_vm(payload, job_id)
        if job_type == "start_vm":
            return self.validate_start_vm(payload, job_id)
        if job_type == "stop_vm":
            return self.validate_stop_vm(payload, job_id)
        if job_type == "snapshot_create":
            return self.validate_snapshot_create(payload, job_id)
        if job_type == "snapshot_restore":
            return self.validate_snapshot_restore(payload, job_id)
        if job_type == "snapshot_delete":
            return self.validate_snapshot_delete(payload, job_id)
        if job_type in ("reconcile_vm_networking", "refresh_vm_runtime_state", "collect_vm_logs"):
            # These jobs are always safe to execute
            return ValidationResult.valid(job_id=job_id)

        return ValidationResult.invalid_payload(
            code=ValidationErrorCode.UNSUPPORTED_JOB_TYPE,
            reason=f"Unsupported job type: {job_type}",
            job_id=job_id,
        )

    def validate_target_host(self, job: dict[str, Any]) -> ValidationResult:
        """Validate job is intended for this worker's host.

        Args:
            job: Job data dictionary

        Returns:
            ValidationResult indicating host targeting validity
        """
        job_id = job.get("id")
        target_host_id = job.get("targetHostId")
        worker_host_id = self.worker_config.host_id

        if not target_host_id:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: targetHostId",
                job_id=job_id,
            )

        if target_host_id != worker_host_id:
            return ValidationResult.wrong_host(
                reason=(
                    f"Job target host '{target_host_id}' does not match "
                    f"worker host '{worker_host_id}'"
                ),
                job_id=job_id,
                observed_state={
                    "job_target_host": target_host_id,
                    "worker_host_id": worker_host_id,
                },
            )

        return ValidationResult.valid(job_id=job_id)

    def validate_job_payload(self, job: dict[str, Any]) -> ValidationResult:
        """Validate job payload structure.

        Args:
            job: Job data dictionary

        Returns:
            ValidationResult indicating payload validity
        """
        job_id = job.get("id")
        job_type = job.get("type")

        if not job_type:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: type",
                job_id=job_id,
            )

        payload = job.get("payload")
        if payload is None:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: payload",
                job_id=job_id,
            )

        if not isinstance(payload, dict):
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.INVALID_FIELD_VALUE,
                reason="Payload must be a dictionary",
                job_id=job_id,
            )

        return ValidationResult.valid(job_id=job_id)

    def _vm_exists(self, vm_name: str) -> bool:
        """Check if VM exists in libvirt.

        Args:
            vm_name: VM name

        Returns:
            True if VM exists, False otherwise
        """
        try:
            # Import here to avoid circular dependency
            from homelab_vm_provisioner.provision import vm_exists

            return vm_exists(vm_name)
        except Exception:
            # If we can't check, assume it doesn't exist (fail-safe)
            return False

    def _disk_exists(self, vm_name: str) -> bool:
        """Check if VM disk file exists.

        Args:
            vm_name: VM name

        Returns:
            True if disk exists, False otherwise
        """
        try:
            # Import here to avoid circular dependency
            from homelab_vm_provisioner.provision import vm_disk_path

            disk_path = vm_disk_path(vm_name)
            return Path(disk_path).exists()
        except Exception:
            # If we can't check, assume it doesn't exist (fail-safe)
            return False

    def _get_vm_status(self, vm_name: str) -> str:
        """Get current VM status.

        Args:
            vm_name: VM name

        Returns:
            Status string (running, shut off, paused, unknown, etc.)
        """
        try:
            runtime_state = self.service_mode.refresh_vm_runtime_state(vm_name)
            return runtime_state.get("status", "unknown")
        except Exception:
            return "unknown"

    def validate_provision_vm(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate VM provision job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: vmName",
                job_id=job_id,
            )

        # Check if VM definition exists in database
        try:
            vm_definition = self.db_client.get_vm_definition_by_name(vm_name)
            if not vm_definition:
                return ValidationResult.invalid_payload(
                    code=ValidationErrorCode.VM_DEFINITION_NOT_FOUND,
                    reason=f"VM definition not found in database: {vm_name}",
                    job_id=job_id,
                    target=vm_name,
                )
        except Exception as e:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.VM_DEFINITION_NOT_FOUND,
                reason=f"Failed to load VM definition: {e}",
                job_id=job_id,
                target=vm_name,
            )

        # Check if VM already exists
        vm_exists_in_libvirt = self._vm_exists(vm_name)
        disk_exists = self._disk_exists(vm_name)

        if vm_exists_in_libvirt and disk_exists:
            # VM and disk both exist - this is a duplicate creation attempt
            return ValidationResult.already_exists(
                code=ValidationErrorCode.VM_ALREADY_EXISTS,
                reason=f"VM already exists on this host; refusing duplicate creation: {vm_name}",
                job_id=job_id,
                target=vm_name,
                observed_state={
                    "vm_exists": True,
                    "disk_exists": True,
                },
            )
        if vm_exists_in_libvirt or disk_exists:
            # Partial state - VM or disk exists but not both
            return ValidationResult.cleanup_required(
                code=ValidationErrorCode.PARTIAL_STATE_DETECTED,
                reason=(
                    f"Partial VM state detected (vm_exists={vm_exists_in_libvirt}, "
                    f"disk_exists={disk_exists}); cleanup required before provisioning: {vm_name}"
                ),
                job_id=job_id,
                target=vm_name,
                observed_state={
                    "vm_exists": vm_exists_in_libvirt,
                    "disk_exists": disk_exists,
                },
            )

        return ValidationResult.valid(job_id=job_id, target=vm_name)

    def validate_clone_vm(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate VM clone job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        source_vm_name = payload.get("sourceVmName")
        target_vm_name = payload.get("targetVmName")

        if not source_vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: sourceVmName",
                job_id=job_id,
            )

        if not target_vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: targetVmName",
                job_id=job_id,
            )

        # Check if source VM exists
        if not self._vm_exists(source_vm_name):
            return ValidationResult.not_found(
                code=ValidationErrorCode.VM_NOT_FOUND,
                reason=f"Source VM not found: {source_vm_name}",
                job_id=job_id,
                target=source_vm_name,
            )

        # Check if source disk exists
        if not self._disk_exists(source_vm_name):
            return ValidationResult.not_found(
                code=ValidationErrorCode.DISK_NOT_FOUND,
                reason=f"Source VM disk not found: {source_vm_name}",
                job_id=job_id,
                target=source_vm_name,
            )

        # Check if target VM definition exists in database
        try:
            vm_definition = self.db_client.get_vm_definition_by_name(target_vm_name)
            if not vm_definition:
                return ValidationResult.invalid_payload(
                    code=ValidationErrorCode.VM_DEFINITION_NOT_FOUND,
                    reason=f"Target VM definition not found in database: {target_vm_name}",
                    job_id=job_id,
                    target=target_vm_name,
                )
        except Exception as e:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.VM_DEFINITION_NOT_FOUND,
                reason=f"Failed to load target VM definition: {e}",
                job_id=job_id,
                target=target_vm_name,
            )

        # Check if target VM already exists
        target_vm_exists = self._vm_exists(target_vm_name)
        target_disk_exists = self._disk_exists(target_vm_name)

        if target_vm_exists and target_disk_exists:
            return ValidationResult.already_exists(
                code=ValidationErrorCode.VM_ALREADY_EXISTS,
                reason=f"Target VM already exists; refusing duplicate clone: {target_vm_name}",
                job_id=job_id,
                target=target_vm_name,
                observed_state={
                    "vm_exists": True,
                    "disk_exists": True,
                },
            )
        if target_vm_exists or target_disk_exists:
            return ValidationResult.cleanup_required(
                code=ValidationErrorCode.PARTIAL_STATE_DETECTED,
                reason=(
                    f"Partial target VM state detected (vm_exists={target_vm_exists}, "
                    f"disk_exists={target_disk_exists}); cleanup required: {target_vm_name}"
                ),
                job_id=job_id,
                target=target_vm_name,
                observed_state={
                    "vm_exists": target_vm_exists,
                    "disk_exists": target_disk_exists,
                },
            )

        return ValidationResult.valid(job_id=job_id, target=target_vm_name)

    def validate_start_vm(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate VM start job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: vmName",
                job_id=job_id,
            )

        # Check if VM exists
        if not self._vm_exists(vm_name):
            return ValidationResult.not_found(
                code=ValidationErrorCode.VM_NOT_FOUND,
                reason=f"VM not found; cannot start: {vm_name}",
                job_id=job_id,
                target=vm_name,
            )

        # Check current status
        status = self._get_vm_status(vm_name)
        if status == "running":
            return ValidationResult.noop_success(
                code=ValidationErrorCode.VM_ALREADY_RUNNING,
                reason=f"VM is already running; treating as no-op: {vm_name}",
                job_id=job_id,
                target=vm_name,
                observed_state={"status": status},
            )

        return ValidationResult.valid(job_id=job_id, target=vm_name)

    def validate_stop_vm(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate VM stop job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: vmName",
                job_id=job_id,
            )

        # Check if VM exists
        if not self._vm_exists(vm_name):
            return ValidationResult.not_found(
                code=ValidationErrorCode.VM_NOT_FOUND,
                reason=f"VM not found; cannot stop: {vm_name}",
                job_id=job_id,
                target=vm_name,
            )

        # Check current status
        status = self._get_vm_status(vm_name)
        if status in ("shut off", "shutoff", "stopped"):
            return ValidationResult.noop_success(
                code=ValidationErrorCode.VM_ALREADY_STOPPED,
                reason=f"VM is already stopped; treating as no-op: {vm_name}",
                job_id=job_id,
                target=vm_name,
                observed_state={"status": status},
            )

        return ValidationResult.valid(job_id=job_id, target=vm_name)

    def validate_destroy_vm(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate VM destroy job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: vmName",
                job_id=job_id,
            )

        # Check VM and disk existence
        vm_exists_in_libvirt = self._vm_exists(vm_name)
        disk_exists = self._disk_exists(vm_name)

        if not vm_exists_in_libvirt and not disk_exists:
            # VM and disk already gone - safe no-op
            return ValidationResult.noop_success(
                code=ValidationErrorCode.VM_NOT_FOUND,
                reason=f"VM and disk already gone; treating as no-op: {vm_name}",
                job_id=job_id,
                target=vm_name,
                observed_state={
                    "vm_exists": False,
                    "disk_exists": False,
                },
            )
        if vm_exists_in_libvirt or disk_exists:
            # At least one component exists - proceed with destruction
            return ValidationResult.valid(job_id=job_id, target=vm_name)

        # Should not reach here, but handle as valid
        return ValidationResult.valid(job_id=job_id, target=vm_name)

    def validate_snapshot_create(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate snapshot create job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        vm_name = payload.get("vmName")
        if not vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: vmName",
                job_id=job_id,
            )

        # Check if VM exists
        if not self._vm_exists(vm_name):
            return ValidationResult.not_found(
                code=ValidationErrorCode.VM_NOT_FOUND,
                reason=f"VM not found; cannot create snapshot: {vm_name}",
                job_id=job_id,
                target=vm_name,
            )

        # Note: Snapshot duplicate checking is handled by the provisioner CLI
        # which generates unique snapshot IDs. We allow snapshot creation here.

        return ValidationResult.valid(job_id=job_id, target=vm_name)

    def validate_snapshot_restore(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate snapshot restore job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        vm_name = payload.get("vmName")
        snapshot_id = payload.get("snapshotId")

        if not vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: vmName",
                job_id=job_id,
            )

        if not snapshot_id:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: snapshotId",
                job_id=job_id,
                target=vm_name,
            )

        # Check if VM exists
        if not self._vm_exists(vm_name):
            return ValidationResult.not_found(
                code=ValidationErrorCode.VM_NOT_FOUND,
                reason=f"VM not found; cannot restore snapshot: {vm_name}",
                job_id=job_id,
                target=f"{vm_name}:{snapshot_id}",
            )

        # Check if snapshot exists in database
        try:
            snapshot_record = self.db_client.get_vm_snapshot(vm_name, snapshot_id)
            if not snapshot_record:
                return ValidationResult.not_found(
                    code=ValidationErrorCode.SNAPSHOT_NOT_FOUND,
                    reason=f"Snapshot not found in database: {vm_name}:{snapshot_id}",
                    job_id=job_id,
                    target=f"{vm_name}:{snapshot_id}",
                )
        except Exception as e:
            return ValidationResult.not_found(
                code=ValidationErrorCode.SNAPSHOT_NOT_FOUND,
                reason=f"Failed to load snapshot: {e}",
                job_id=job_id,
                target=f"{vm_name}:{snapshot_id}",
            )

        return ValidationResult.valid(job_id=job_id, target=f"{vm_name}:{snapshot_id}")

    def validate_snapshot_delete(self, payload: dict[str, Any], job_id: int | None = None) -> ValidationResult:
        """Validate snapshot delete job.

        Args:
            payload: Job payload
            job_id: Job ID

        Returns:
            ValidationResult
        """
        vm_name = payload.get("vmName")
        snapshot_id = payload.get("snapshotId")

        if not vm_name:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: vmName",
                job_id=job_id,
            )

        if not snapshot_id:
            return ValidationResult.invalid_payload(
                code=ValidationErrorCode.MISSING_REQUIRED_FIELD,
                reason="Missing required field: snapshotId",
                job_id=job_id,
                target=vm_name,
            )

        # Check if snapshot exists in database
        try:
            snapshot_record = self.db_client.get_vm_snapshot(vm_name, snapshot_id)
            if not snapshot_record:
                # Snapshot already gone - safe no-op
                return ValidationResult.noop_success(
                    code=ValidationErrorCode.SNAPSHOT_NOT_FOUND,
                    reason=f"Snapshot already deleted; treating as no-op: {vm_name}:{snapshot_id}",
                    job_id=job_id,
                    target=f"{vm_name}:{snapshot_id}",
                )
        except Exception:
            # If we can't check, allow deletion to proceed (it will fail safely in executor)
            pass

        return ValidationResult.valid(job_id=job_id, target=f"{vm_name}:{snapshot_id}")
