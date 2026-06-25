"""Worker configuration for job queue daemon.

Reads configuration from environment variables and config files.
"""

import os
import socket
from pathlib import Path
from typing import Optional


class WorkerConfig:
    """Configuration for the worker daemon."""

    def __init__(
        self,
        database_url: str,
        host_id: str,
        api_host: str,
        api_port: int,
        worker_id: Optional[str] = None,
        concurrency: int = 1,
        state_refresh_interval: float = 60.0,
        db_service_url: Optional[str] = None,
        db_service_password: Optional[str] = None,
        provisioner_cli_path: Optional[str] = None,
    ):
        """Initialize worker configuration.

        Args:
            database_url: PostgreSQL connection URL (deprecated, use db_service_url)
            host_id: Host identifier for job claiming
            api_host: API host (required, e.g. localhost)
            api_port: API port (required, e.g. 3001)
            worker_id: Unique worker identifier (auto-generated if None)
            concurrency: Maximum number of concurrent jobs (default: 1)
            state_refresh_interval: Runtime-state refresh interval in seconds
            db_service_url: Database microservice URL (preferred)
            db_service_password: Database microservice password
            provisioner_cli_path: Path to provisioner CLI (None = use PATH)
        """
        self.database_url = database_url
        self.host_id = host_id
        self.api_host = api_host
        self.api_port = api_port
        self.api_url = f"http://{api_host}:{api_port}"
        self.worker_id = worker_id or self._generate_worker_id()
        self.concurrency = max(1, concurrency)
        self.state_refresh_interval = max(5.0, state_refresh_interval)
        self.db_service_url = db_service_url
        self.db_service_password = db_service_password
        self.provisioner_cli_path = self._resolve_provisioner_path(provisioner_cli_path)

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
            cli_path: Configured CLI path (REQUIRED for standalone microservice operation)

        Returns:
            Absolute path to provisioner CLI directory

        Raises:
            ValueError: If CLI path is not provided
        """
        if not cli_path:
            raise ValueError(
                "PROVISIONER_CLI_PATH must be explicitly configured. "
                "Worker is a standalone microservice and cannot assume provisioner location."
            )

        # Use configured path
        path = Path(cli_path).resolve()
        if not path.exists():
            raise ValueError(f"Provisioner CLI path does not exist: {cli_path}")

        return str(path)

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        """Load worker configuration from environment variables.

        Environment variables:
            DATABASE_URL: PostgreSQL connection URL (deprecated)
            DB_SERVICE_URL: Database microservice URL (preferred)
            DB_SERVICE_PASSWORD: Database microservice password
            HOST_ID: Host identifier for job claiming
            API_HOST: API host (required, e.g. localhost)
            API_PORT: API port (required, e.g. 3001)
            WORKER_QUEUE_HOST: RabbitMQ host (required)
            WORKER_ID: Optional worker identifier (auto-generated if not set)
            PROVISIONER_CONCURRENCY: Max concurrent jobs (default: 1)
            WORKER_STATE_REFRESH_INTERVAL: Runtime-state refresh interval in seconds (default: 60.0)
            PROVISIONER_CLI_PATH: Path to provisioner CLI (optional)

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

        api_host = os.environ.get("API_HOST", "")
        api_port_str = os.environ.get("API_PORT", "")

        if not api_host:
            raise ValueError("API_HOST environment variable is required")
        if not api_port_str:
            raise ValueError("API_PORT environment variable is required")

        try:
            api_port = int(api_port_str)
        except ValueError as e:
            raise ValueError(f"API_PORT must be a valid integer, got: {api_port_str}") from e

        # Validate RabbitMQ configuration is present
        rabbitmq_host = os.environ.get("WORKER_QUEUE_HOST", "")
        if not rabbitmq_host:
            raise ValueError(
                "WORKER_QUEUE_HOST environment variable is required. "
                "Worker requires RabbitMQ for job consumption."
            )

        worker_id = os.environ.get("WORKER_ID", None)
        concurrency = int(os.environ.get("PROVISIONER_CONCURRENCY", "1"))
        state_refresh_interval = float(os.environ.get("WORKER_STATE_REFRESH_INTERVAL", "60.0"))
        provisioner_cli_path = os.environ.get("PROVISIONER_CLI_PATH", None)

        return cls(
            database_url=database_url,
            host_id=host_id,
            api_host=api_host,
            api_port=api_port,
            worker_id=worker_id,
            concurrency=concurrency,
            state_refresh_interval=state_refresh_interval,
            db_service_url=db_service_url,
            db_service_password=db_service_password,
            provisioner_cli_path=provisioner_cli_path,
        )

    def __repr__(self) -> str:
        """Return string representation of worker configuration."""
        return (
            f"WorkerConfig(host_id={self.host_id!r}, "
            f"worker_id={self.worker_id!r}, concurrency={self.concurrency}, "
            f"state_refresh_interval={self.state_refresh_interval}, "
            f"api_host={self.api_host!r}, api_port={self.api_port}, "
            f"provisioner_cli_path={self.provisioner_cli_path!r})"
        )
