"""Worker daemon for processing VM provisioning jobs.

Long-running process that claims and executes queued jobs from PostgreSQL.
"""

import logging
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from hlvmp_worker.config import WorkerConfig
from hlvmp_worker.db_client import DatabaseClient
from hlvmp_worker.executor import JobExecutionError, JobExecutor
from hlvmp_worker.socket_server import SocketServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("worker")


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
        self.active_jobs: set[int] = set()
        self.shutdown_requested = False
        self.socket_server: Optional[SocketServer] = None
        self.wake_event = threading.Event()  # Event for socket wakeups

    def _handle_signal(self, signum, frame):  # noqa: ARG002
        """Handle shutdown signals.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True
        # Wake the main loop to process shutdown quickly
        self.wake_event.set()

    def _on_socket_wake(self):
        """Handle wake message from socket server.

        This is called by the socket server when a wake message is received.
        It triggers an immediate job scan by setting the wake event.
        """
        logger.info("Socket wake received, triggering immediate job scan")
        self.wake_event.set()

    def _on_socket_health(self) -> dict:
        """Handle health message from socket server.

        Returns:
            Health status dictionary
        """
        return {
            "status": "ok",
            "worker_id": self.config.worker_id,
            "host_id": self.config.host_id,
            "concurrency": self.config.concurrency,
            "active_jobs": len(self.active_jobs),
            "available_slots": self.config.concurrency - len(self.active_jobs),
        }

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

            # Mark job as succeeded
            self.db_client.mark_job_succeeded(job_id, result)
            self._log_job_event(job_id, "info", "Job completed successfully", result)

            return True

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

            # Remove from active jobs
            self.active_jobs.discard(job_id)

    def run(self):
        """Run the worker daemon.

        This is the main event loop that continuously claims and processes jobs.
        """
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

        # Start socket server if configured
        if self.config.socket_path:
            try:
                self.socket_server = SocketServer(
                    self.config.socket_path,
                    on_wake=self._on_socket_wake,
                    on_health=self._on_socket_health,
                )
                self.socket_server.start()
                logger.info(f"Socket server enabled at {self.config.socket_path}")
            except Exception as e:
                logger.warning(f"Failed to start socket server: {e}")
                logger.warning("Continuing without socket server, using fallback polling only")
                self.socket_server = None
        else:
            logger.info("Socket server not configured, using fallback polling only")

        self.running = True

        # Thread pool for concurrent job execution
        with ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
            futures = []

            try:
                while not self.shutdown_requested:
                    # Clean up completed futures
                    futures = [f for f in futures if not f.done()]

                    # Check if we can claim more jobs
                    available_slots = self.config.concurrency - len(futures)

                    if available_slots > 0:
                        try:
                            # Try to claim a job
                            job = self.db_client.claim_next_job(
                                self.config.host_id, self.config.worker_id
                            )

                            if job:
                                job_id = job["id"]
                                self.active_jobs.add(job_id)
                                logger.debug(f"Claimed job {job_id}, submitting to executor")

                                # Submit job to thread pool
                                future = executor.submit(self._process_job, job)
                                futures.append(future)

                                # Clear wake event after claiming a job
                                self.wake_event.clear()
                            else:
                                # No jobs available, wait for wake or poll interval
                                # Use wake_event.wait() with timeout for efficient waiting
                                self.wake_event.wait(timeout=self.config.poll_interval)
                                self.wake_event.clear()

                        except Exception as e:
                            logger.error(f"Error in main loop: {e}", exc_info=True)
                            # Wait before retrying on error
                            self.wake_event.wait(timeout=self.config.poll_interval)
                            self.wake_event.clear()
                    else:
                        # All slots full, wait a bit or for wake
                        self.wake_event.wait(timeout=1.0)
                        self.wake_event.clear()

            finally:
                # Shutdown: wait for active jobs to complete
                logger.info("Shutdown requested, waiting for active jobs to complete...")
                logger.info(f"Active jobs: {self.active_jobs}")

                # Wait for all futures to complete
                for future in as_completed(futures, timeout=300):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Job execution error during shutdown: {e}")

                # Stop socket server
                if self.socket_server:
                    self.socket_server.stop()

        logger.info("Worker daemon stopped")
        self.running = False


def main():  # pragma: no cover
    """Main entry point for worker daemon."""
    try:
        # Load configuration from environment
        config = WorkerConfig.from_env()

        # Create database client
        db_service_url = config.db_service_url or config.database_url
        db_service_password = config.db_service_password or ""

        if not db_service_url:
            logger.error("No database service URL configured")
            sys.exit(1)

        db_client = DatabaseClient(db_service_url, db_service_password)

        # Create job executor
        executor = JobExecutor(config.provisioner_cli_path)

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
