"""Worker configuration for job queue daemon.

Reads configuration from environment variables and config files.
"""

import os
import shutil
import socket
from pathlib import Path
from typing import Optional


class WorkerConfig:
    """Configuration for the worker daemon."""

    def __init__(
        self,
        database_url: str,
        host_id: str,
        worker_id: Optional[str] = None,
        concurrency: int = 1,
        poll_interval: float = 5.0,
        db_service_url: Optional[str] = None,
        db_service_password: Optional[str] = None,
        provisioner_cli_path: Optional[str] = None,
        socket_path: Optional[str] = None,
    ):
        """Initialize worker configuration.

        Args:
            database_url: PostgreSQL connection URL (deprecated, use db_service_url)
            host_id: Host identifier for job claiming
            worker_id: Unique worker identifier (auto-generated if None)
            concurrency: Maximum number of concurrent jobs (default: 1)
            poll_interval: Job poll interval in seconds (default: 5.0)
            db_service_url: Database microservice URL (preferred)
            db_service_password: Database microservice password
            provisioner_cli_path: Path to provisioner CLI (None = use PATH)
            socket_path: Unix socket path for wakeup mechanism (None = disabled)
        """
        self.database_url = database_url
        self.host_id = host_id
        self.worker_id = worker_id or self._generate_worker_id()
        self.concurrency = max(1, concurrency)
        self.poll_interval = max(1.0, poll_interval)
        self.db_service_url = db_service_url
        self.db_service_password = db_service_password
        self.provisioner_cli_path = self._resolve_provisioner_path(provisioner_cli_path)
        self.socket_path = socket_path

    def _generate_worker_id(self) -> str:
        """Generate a stable worker ID based on hostname and PID.

        Returns:
            Worker ID string
        """
        hostname = socket.gethostname()
        pid = os.getpid()
        return f"{hostname}-{pid}"

    def _resolve_provisioner_path(self, cli_path: Optional[str]) -> str:
        """Resolve path to provisioner CLI.

        Args:
            cli_path: Configured CLI path or None

        Returns:
            Absolute path to provisioner CLI directory

        Raises:
            ValueError: If CLI path cannot be resolved
        """
        if cli_path:
            # Use configured path
            path = Path(cli_path).resolve()
            if not path.exists():
                raise ValueError(f"Provisioner CLI path does not exist: {cli_path}")
            return str(path)

        # Try to find vmctl in PATH
        vmctl_path = shutil.which("vmctl")
        if vmctl_path:
            # vmctl found, return its parent directory
            return str(Path(vmctl_path).parent.resolve())

        raise ValueError(
            "Could not find provisioner CLI. Set PROVISIONER_CLI_PATH or ensure vmctl is in PATH."
        )

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        """Load worker configuration from environment variables.

        Environment variables:
            DATABASE_URL: PostgreSQL connection URL (deprecated)
            DB_SERVICE_URL: Database microservice URL (preferred)
            DB_SERVICE_PASSWORD: Database microservice password
            HOST_ID: Host identifier for job claiming
            WORKER_ID: Optional worker identifier (auto-generated if not set)
            PROVISIONER_CONCURRENCY: Max concurrent jobs (default: 1)
            WORKER_POLL_INTERVAL: Poll interval in seconds (default: 5.0)
            PROVISIONER_CLI_PATH: Path to provisioner CLI (optional)
            WORKER_SOCKET: Unix socket path for wakeup mechanism (optional)

        Returns:
            WorkerConfig instance

        Raises:
            ValueError: If required configuration is missing
        """
        database_url = os.environ.get("DATABASE_URL", "")
        db_service_url = os.environ.get("DB_SERVICE_URL", "")
        db_service_password = os.environ.get("DB_SERVICE_PASSWORD", "")
        host_id = os.environ.get("HOST_ID", "")

        if not host_id:
            raise ValueError("HOST_ID environment variable is required")

        if not database_url and not db_service_url:
            raise ValueError(
                "Either DATABASE_URL or DB_SERVICE_URL environment variable is required"
            )

        worker_id = os.environ.get("WORKER_ID", None)
        concurrency = int(os.environ.get("PROVISIONER_CONCURRENCY", "1"))
        poll_interval = float(os.environ.get("WORKER_POLL_INTERVAL", "5.0"))
        provisioner_cli_path = os.environ.get("PROVISIONER_CLI_PATH", None)
        socket_path = os.environ.get("WORKER_SOCKET", None)

        return cls(
            database_url=database_url,
            host_id=host_id,
            worker_id=worker_id,
            concurrency=concurrency,
            poll_interval=poll_interval,
            db_service_url=db_service_url,
            db_service_password=db_service_password,
            provisioner_cli_path=provisioner_cli_path,
            socket_path=socket_path,
        )

    def __repr__(self) -> str:
        """Return string representation of worker configuration."""
        return (
            f"WorkerConfig(host_id={self.host_id!r}, "
            f"worker_id={self.worker_id!r}, concurrency={self.concurrency}, "
            f"poll_interval={self.poll_interval}, "
            f"provisioner_cli_path={self.provisioner_cli_path!r})"
        )
