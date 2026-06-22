"""Tests for worker configuration."""

import os
import unittest
from unittest.mock import patch

from hlvmp_worker.config import WorkerConfig


class TestWorkerConfig(unittest.TestCase):
    """Test worker configuration."""

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                provisioner_cli_path="/usr/bin/vmctl",
            )

        self.assertEqual(config.database_url, "postgresql://localhost/test")
        self.assertEqual(config.host_id, "test-host")
        self.assertIsNotNone(config.worker_id)
        self.assertEqual(config.concurrency, 1)
        self.assertEqual(config.poll_interval, 5.0)
        self.assertIn("/usr/bin", config.provisioner_cli_path)

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                worker_id="custom-worker",
                concurrency=3,
                poll_interval=10.0,
                provisioner_cli_path="/custom/path",
            )

        self.assertEqual(config.worker_id, "custom-worker")
        self.assertEqual(config.concurrency, 3)
        self.assertEqual(config.poll_interval, 10.0)
        self.assertIn("custom", config.provisioner_cli_path)

    def test_concurrency_minimum(self):
        """Test that concurrency is clamped to minimum of 1."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                concurrency=0,
                provisioner_cli_path="/usr/bin/vmctl",
            )

        self.assertEqual(config.concurrency, 1)

    def test_poll_interval_minimum(self):
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                poll_interval=0.5,
                provisioner_cli_path="/usr/bin/vmctl",
        )

        self.assertEqual(config.poll_interval, 1.0)

    @patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://localhost/test",
            "HOST_ID": "env-host",
            "WORKER_ID": "env-worker",
            "PROVISIONER_CONCURRENCY": "2",
            "WORKER_POLL_INTERVAL": "7.5",
            "PROVISIONER_CLI_PATH": "/usr/bin/vmctl",
        },
    )
    @patch("hlvmp_worker.config.Path.exists", return_value=True)
    def test_from_env(self, mock_exists):
        """Test loading configuration from environment variables."""
        config = WorkerConfig.from_env()

        self.assertEqual(config.database_url, "postgresql://localhost/test")
        self.assertEqual(config.host_id, "env-host")
        self.assertEqual(config.worker_id, "env-worker")
        self.assertEqual(config.concurrency, 2)
        self.assertEqual(config.poll_interval, 7.5)

    @patch.dict(
        os.environ,
        {
            "DB_SERVICE_URL": "http://localhost:3002",
            "DB_SERVICE_PASSWORD": "secret",
            "HOST_ID": "env-host",
            "PROVISIONER_CLI_PATH": "/usr/bin/vmctl",
        },
    )
    @patch("hlvmp_worker.config.Path.exists", return_value=True)
    def test_from_env_with_db_service(self, mock_exists):
        """Test loading with DB service URL instead of DATABASE_URL."""
        config = WorkerConfig.from_env()

        self.assertEqual(config.db_service_url, "http://localhost:3002")
        self.assertEqual(config.db_service_password, "secret")
        self.assertEqual(config.host_id, "env-host")

    @patch.dict(os.environ, {}, clear=True)
    def test_from_env_missing_host_id(self):
        """Test that from_env raises ValueError when HOST_ID is missing."""
        with self.assertRaises(ValueError) as context:
            WorkerConfig.from_env()

        self.assertIn("HOST_ID", str(context.exception))

    @patch.dict(os.environ, {"HOST_ID": "test-host"}, clear=True)
    def test_from_env_missing_database_url(self):
        """Test that from_env raises ValueError when both URLs are missing."""
        with self.assertRaises(ValueError) as context:
            WorkerConfig.from_env()

        self.assertIn("DATABASE_URL", str(context.exception))
        self.assertIn("DB_SERVICE_URL", str(context.exception))

    def test_init_with_explicit_provisioner_path(self):
        """Test initialization with explicit provisioner CLI path."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                worker_id="test-worker",
                provisioner_cli_path="/test/path",
            )

        repr_str = repr(config)

        self.assertIn("test-host", repr_str)
        self.assertIn("test-worker", repr_str)
        self.assertIn("concurrency=1", repr_str)
        self.assertIn("provisioner_cli_path", repr_str)
        self.assertIn("test-worker", repr_str)
        self.assertIn("concurrency=1", repr_str)


if __name__ == "__main__":
    unittest.main()
