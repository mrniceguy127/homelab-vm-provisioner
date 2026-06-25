"""Worker daemon for processing VM provisioning jobs.

Long-running process that claims and executes queued jobs from PostgreSQL.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

import requests

from hlvmp_worker.config import WorkerConfig
from hlvmp_worker.db_client import DatabaseClient
from hlvmp_worker.executor import JobExecutionError, JobExecutor, JobValidationError
from hlvmp_worker.rabbitmq_consumer import RabbitMqConsumer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("worker")

# Sudo keepalive interval (60 seconds)
SUDO_KEEPALIVE_INTERVAL_S = 60


def ensure_sudo_credentials():
    """Ensure sudo credentials are available for worker operations.

    The worker needs sudo to execute provisioner CLI commands that interact
    with libvirt and nftables. This function always prompts for sudo access
    on startup to ensure proper credentials.

    Raises:
        RuntimeError: If unable to acquire sudo credentials
    """
    logger.info("=" * 70)
    logger.info("HOMELAB VM PROVISIONER WORKER")
    logger.info("=" * 70)
    logger.info("")
    logger.info("This worker daemon requires sudo access to:")
    logger.info("  • Manage libvirt VMs and networks")
    logger.info("  • Configure nftables firewall rules")
    logger.info("  • Execute provisioner CLI operations")
    logger.info("")
    logger.info("You will be prompted for your sudo password.")
    logger.info("=" * 70)

    # Always validate sudo access on startup (prompts if needed)
    result = subprocess.run(
        ["sudo", "-v"],
        check=False,
    )

    if result.returncode != 0:
        logger.error("Failed to acquire sudo credentials")
        raise RuntimeError("Unable to acquire sudo credentials for worker operations")

    logger.info("✓ Sudo credentials acquired successfully")
    logger.info("")


def start_sudo_keepalive():
    """Start background thread to refresh sudo credentials periodically.

    This prevents sudo credentials from expiring while the worker is running.
    The thread runs as a daemon so it won't prevent the process from exiting.
    """
    def refresh_sudo():
        """Background task to refresh sudo credentials."""
        while True:
            try:
                time.sleep(SUDO_KEEPALIVE_INTERVAL_S)
                subprocess.run(
                    ["sudo", "-n", "-v"],
                    capture_output=True,
                    check=False,
                )
            except Exception as e:
                logger.warning(f"Sudo keepalive refresh failed: {e}")
                # Continue running - credentials may be valid longer than timeout

    thread = threading.Thread(target=refresh_sudo, daemon=True, name="sudo-keepalive")
    thread.start()
    logger.info(f"Sudo keepalive started (refresh interval: {SUDO_KEEPALIVE_INTERVAL_S}s)")


class WorkerDaemon:
    """Worker daemon for processing VM provisioning jobs."""

    def __init__(self, config: WorkerConfig, db_client: DatabaseClient, executor: JobExecutor):
        """Initialize worker daemon.

        Args:
            config: Worker configuration
            db_client: Database client for job operations
            executor: Job executor for running provisioner operations
        """
        self.config = config
        self.db_client = db_client
        self.executor = executor
        self.running = False
        self.shutdown_requested = False
        self.rabbitmq_consumer: Optional[RabbitMqConsumer] = None
        self._next_state_refresh_at = time.monotonic() + self.config.state_refresh_interval

    def _handle_signal(self, signum, frame):  # noqa: ARG002
        """Handle shutdown signals.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True
        if self.rabbitmq_consumer:
            self.rabbitmq_consumer.stop_consuming()

    def _log_job_event(
        self, job_id: int, level: str, message: str, metadata: Optional[dict] = None
    ):
        """Log an event to the job's event log.

        Args:
            job_id: Job ID
            level: Event level (debug, info, warning, error)
            message: Event message
            metadata: Optional event metadata
        """
        try:
            self.db_client.append_job_event(job_id, level, message, metadata)
        except Exception as e:
            logger.error(f"Failed to log job event for job {job_id}: {e}")

    def _process_job(self, job: dict) -> bool:
        """Process a single job.

        Args:
            job: Job data dictionary

        Returns:
            True if job succeeded, False otherwise
        """
        job_id = job["id"]
        job_type = job["type"]

        logger.info(f"Processing job {job_id} (type: {job_type})")
        self._log_job_event(job_id, "info", f"Worker {self.config.worker_id} started processing job")

        # Acquire resource locks
        lock_keys = self.executor.get_resource_locks(job)
        logger.debug(f"Job {job_id} requires locks: {lock_keys}")

        if lock_keys:
            try:
                acquired = self.db_client.acquire_resource_locks(
                    job_id, self.config.worker_id, lock_keys, ttl_ms=300000
                )
                if not acquired:
                    logger.warning(f"Failed to acquire locks for job {job_id}, skipping")
                    self._log_job_event(
                        job_id, "warning", "Failed to acquire resource locks, will retry later"
                    )
                    return False
                logger.debug(f"Acquired locks for job {job_id}: {lock_keys}")
            except Exception as e:
                logger.error(f"Error acquiring locks for job {job_id}: {e}")
                self._log_job_event(job_id, "error", f"Lock acquisition error: {e}")
                return False

        try:
            # Mark job as running
            self.db_client.mark_job_running(job_id, self.config.worker_id)
            self._log_job_event(job_id, "info", "Job marked as running")

            # Execute job
            logger.info(f"Executing job {job_id}")
            result = self.executor.execute_job(job)
            logger.info(f"Job {job_id} completed successfully")

            if not isinstance(result, dict):
                result = {"success": bool(result)}

            # Handle no-op success from validation
            if result.get("noop"):
                logger.info(f"Job {job_id} completed as no-op: {result.get('reason')}")
                self._log_job_event(
                    job_id,
                    "info",
                    f"Job no-op: {result.get('reason')}",
                    {
                        "validation_status": result.get("validation_status"),
                        "validation_code": result.get("validation_code"),
                    }
                )
                self.db_client.mark_job_succeeded(job_id, result)
                return True

            vm_name = result.get("vmName") or job.get("targetVmId")
            snapshot_id = result.get("snapshotId")
            observation_source = result.get("observationSource", "worker_mutation")

            if vm_name and result.get("deleteRuntimeState"):
                self.db_client.delete_vm_runtime_state(vm_name)
            elif vm_name and result.get("runtimeState"):
                self.db_client.upsert_vm_runtime_state(vm_name, result["runtimeState"], observation_source)
            elif vm_name and result.get("runtimeStatePatch"):
                current_runtime_state = self.db_client.get_vm_runtime_state(vm_name)
                next_state = dict((current_runtime_state or {}).get("state") or {})
                next_state.update(result["runtimeStatePatch"])
                self.db_client.upsert_vm_runtime_state(vm_name, next_state, observation_source)

            if vm_name and snapshot_id and result.get("snapshotRecord"):
                self.db_client.upsert_vm_snapshot(vm_name, snapshot_id, result["snapshotRecord"])
            elif vm_name and snapshot_id and result.get("deleteSnapshotRecord"):
                self.db_client.delete_vm_snapshot(vm_name, snapshot_id)

            # Mark job as succeeded
            self.db_client.mark_job_succeeded(job_id, result)
            self._log_job_event(job_id, "info", "Job completed successfully", result)

            return True

        except JobValidationError as e:
            logger.warning(f"Job {job_id} validation failed: {e}")
            validation_result = e.validation_result

            self._log_job_event(
                job_id,
                "warning" if validation_result.requires_cleanup else "error",
                f"Job validation failed: {validation_result.reason}",
                validation_result.to_dict()
            )

            # Mark job as failed with validation details
            error_message = (
                f"Validation failed ({validation_result.code.value if validation_result.code else 'UNKNOWN'}): "
                f"{validation_result.reason}"
            )
            self.db_client.mark_job_failed(job_id, error_message, retriable=e.retriable)

            return False

        except JobExecutionError as e:
            logger.error(f"Job {job_id} failed: {e}")
            self._log_job_event(job_id, "error", f"Job execution failed: {e}")

            # Mark job as failed
            self.db_client.mark_job_failed(job_id, str(e), retriable=e.retriable)

            return False

        except Exception as e:
            logger.error(f"Unexpected error processing job {job_id}: {e}", exc_info=True)
            self._log_job_event(job_id, "error", f"Unexpected error: {e}")

            # Mark job as failed (retriable for unexpected errors)
            try:
                self.db_client.mark_job_failed(job_id, str(e), retriable=True)
            except Exception as mark_error:
                logger.error(f"Failed to mark job {job_id} as failed: {mark_error}")

            return False

        finally:
            # Release resource locks
            if lock_keys:
                try:
                    released = self.db_client.release_resource_locks(job_id, self.config.worker_id)
                    logger.debug(f"Released {released} locks for job {job_id}")
                except Exception as e:
                    logger.error(f"Error releasing locks for job {job_id}: {e}")



    def _refresh_runtime_state_caches(self):
        """Refresh cached runtime state for all known service-managed VMs."""
        try:
            refreshed = self.executor.refresh_all_runtime_state_caches()
            for entry in refreshed:
                self.db_client.upsert_vm_runtime_state(
                    entry["vmName"],
                    entry["runtimeState"],
                    "background_refresh"
                )
        except Exception as e:
            logger.warning(f"Runtime state refresh failed: {e}")

    def _call_api(self, method: str, endpoint: str, json_data: Optional[dict] = None) -> dict:
        """Call API internal endpoint.

        Args:
            method: HTTP method (GET, POST)
            endpoint: API endpoint path (e.g., '/worker/jobs/123/start')
            json_data: JSON request body (optional)

        Returns:
            Response JSON as dict

        Raises:
            requests.exceptions.RequestException: On API call failure
        """
        url = f"{self.config.api_url}/internal{endpoint}"
        response = requests.request(method, url, json=json_data, timeout=30)
        response.raise_for_status()
        return response.json()

    def _process_rabbitmq_message(self, message: dict) -> bool:
        """Process RabbitMQ job message.

        Args:
            message: Job message from RabbitMQ (contains job_id, job_type, target_host_id)

        Returns:
            True to ACK message, False to NACK
        """
        job_id = message.get("job_id")
        target_host_id = message.get("target_host_id")

        if not job_id:
            logger.error(f"Message missing job_id: {message}")
            return False  # NACK invalid message

        # Verify target host matches
        if target_host_id != self.config.host_id:
            logger.warning(f"Job {job_id} targets {target_host_id}, but worker is {self.config.host_id}")
            return False  # NACK, job not for this host

        logger.info(f"Processing RabbitMQ job {job_id}")

        try:
            # Fetch full job details from API
            job = self._call_api("GET", f"/worker/jobs/{job_id}")

            # Mark job as started
            self._call_api(
                "POST",
                f"/worker/jobs/{job_id}/start",
                {"worker_id": self.config.worker_id, "worker_host_id": self.config.host_id}
            )

            # Process job using existing logic
            success = self._process_job(job)

            if success:
                logger.info(f"RabbitMQ job {job_id} succeeded")
                return True  # ACK message
            logger.error(f"RabbitMQ job {job_id} failed")
            # Job already marked as failed by _process_job, just NACK without requeue
            return False

        except requests.exceptions.RequestException as e:
            logger.error(f"API call failed for job {job_id}: {e}")
            # Don't ACK - message will be requeued for retry
            raise
        except Exception as e:
            logger.error(f"Unexpected error processing RabbitMQ job {job_id}: {e}", exc_info=True)
            try:
                # Try to mark job as failed via API
                self._call_api(
                    "POST",
                    f"/worker/jobs/{job_id}/fail",
                    {"error": str(e), "retryable": True}
                )
            except Exception as api_error:
                logger.error(f"Failed to mark job {job_id} as failed: {api_error}")
            # Don't ACK - message will be requeued
            raise

    def run(self):
        """Run the worker daemon.

        This is the main event loop that continuously claims and processes jobs.
        """
        # Check we're not running as root
        if os.geteuid() == 0:
            logger.error("Worker should not run as root. Start as normal user with sudo access.")
            sys.exit(1)

        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info(f"Worker daemon starting (ID: {self.config.worker_id})")
        logger.info(f"Configuration: {self.config}")
        logger.info(f"Supported job types: {self.executor.get_supported_job_types()}")

        # Health check
        if not self.db_client.health_check():
            logger.error("Database microservice health check failed")
            sys.exit(1)

        logger.info("Database microservice is healthy")
        logger.info("Starting RabbitMQ consumer mode")

        self.running = True

        try:
            # Create and connect RabbitMQ consumer
            self.rabbitmq_consumer = RabbitMqConsumer.from_env()
            self.rabbitmq_consumer.connect()

            # Start consuming (blocking until shutdown)
            self.rabbitmq_consumer.consume(
                callback=self._process_rabbitmq_message,
                prefetch_count=self.config.concurrency
            )

        except KeyboardInterrupt:
            logger.info("RabbitMQ consumer interrupted")
        except Exception as e:
            logger.error(f"RabbitMQ consumer error: {e}", exc_info=True)
            raise
        finally:
            if self.rabbitmq_consumer:
                self.rabbitmq_consumer.close()
            logger.info("Worker daemon stopped")
            self.running = False


def main():  # pragma: no cover
    """Main entry point for worker daemon."""
    try:
        # Load configuration from environment
        config = WorkerConfig.from_env()

        # Ensure sudo credentials are available
        logger.info("Checking sudo credentials...")
        ensure_sudo_credentials()

        # Start sudo keepalive background thread
        start_sudo_keepalive()

        # Create database client
        db_service_url = config.db_service_url or config.database_url
        db_service_password = config.db_service_password or ""

        if not db_service_url:
            logger.error("No database service URL configured")
            sys.exit(1)

        db_client = DatabaseClient(db_service_url, db_service_password)

        # Create job executor with worker config for validation
        executor = JobExecutor(config.provisioner_cli_path, db_client, worker_config=config)

        # Create and run worker daemon
        worker = WorkerDaemon(config, db_client, executor)
        worker.run()

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
