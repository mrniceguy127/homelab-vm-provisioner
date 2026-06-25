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
                api_host="localhost",
                api_port=3001,
                provisioner_cli_path="/usr/bin/vmctl",
            )

        self.assertEqual(config.database_url, "postgresql://localhost/test")
        self.assertEqual(config.host_id, "test-host")
        self.assertEqual(config.api_host, "localhost")
        self.assertEqual(config.api_port, 3001)
        self.assertEqual(config.api_url, "http://localhost:3001")
        self.assertIsNotNone(config.worker_id)
        self.assertEqual(config.concurrency, 1)
        self.assertIn("/usr/bin", config.provisioner_cli_path)

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                api_host="localhost",
                api_port=3001,
                worker_id="custom-worker",
                concurrency=3,
                provisioner_cli_path="/custom/path",
            )

        self.assertEqual(config.worker_id, "custom-worker")
        self.assertEqual(config.concurrency, 3)
        self.assertIn("custom", config.provisioner_cli_path)

    def test_concurrency_minimum(self):
        """Test that concurrency is clamped to minimum of 1."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                api_host="localhost",
                api_port=3001,
                concurrency=0,
                provisioner_cli_path="/usr/bin/vmctl",
            )

        self.assertEqual(config.concurrency, 1)

    @patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://localhost/test",
            "HOST_ID": "env-host",
            "API_HOST": "localhost",
            "API_PORT": "3001",
            "WORKER_QUEUE_HOST": "localhost",
            "WORKER_ID": "env-worker",
            "PROVISIONER_CONCURRENCY": "2",
            "PROVISIONER_CLI_PATH": "/usr/bin/vmctl",
        },
    )
    @patch("hlvmp_worker.config.Path.exists", return_value=True)
    def test_from_env(self, mock_exists):
        """Test loading configuration from environment variables."""
        config = WorkerConfig.from_env()

        self.assertEqual(config.database_url, "postgresql://localhost/test")
        self.assertEqual(config.host_id, "env-host")
        self.assertEqual(config.api_host, "localhost")
        self.assertEqual(config.api_port, 3001)
        self.assertEqual(config.api_url, "http://localhost:3001")
        self.assertEqual(config.worker_id, "env-worker")
        self.assertEqual(config.concurrency, 2)

    @patch.dict(
        os.environ,
        {
            "DB_SERVICE_URL": "http://localhost:3002",
            "DB_SERVICE_PASSWORD": "secret",
            "HOST_ID": "env-host",
            "API_HOST": "localhost",
            "API_PORT": "3001",
            "WORKER_QUEUE_HOST": "localhost",
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

    @patch.dict(
        os.environ,
        {
            "HOST_ID": "test-host",
            "DB_SERVICE_URL": "http://localhost:3002",
        },
        clear=True,
    )
    def test_from_env_missing_api_host(self):
        """Test that from_env raises ValueError when API_HOST is missing."""
        with self.assertRaises(ValueError) as context:
            WorkerConfig.from_env()

        self.assertIn("API_HOST", str(context.exception))

    @patch.dict(
        os.environ,
        {
            "HOST_ID": "test-host",
            "DB_SERVICE_URL": "http://localhost:3002",
            "API_HOST": "localhost",
        },
        clear=True,
    )
    def test_from_env_missing_api_port(self):
        """Test that from_env raises ValueError when API_PORT is missing."""
        with self.assertRaises(ValueError) as context:
            WorkerConfig.from_env()

        self.assertIn("API_PORT", str(context.exception))

    @patch.dict(
        os.environ,
        {
            "HOST_ID": "test-host",
            "DB_SERVICE_URL": "http://localhost:3002",
            "API_HOST": "localhost",
            "API_PORT": "not-a-number",
            "WORKER_QUEUE_HOST": "localhost",
            "PROVISIONER_CLI_PATH": "/usr/bin/vmctl",
        },
        clear=True,
    )
    @patch("hlvmp_worker.config.Path.exists", return_value=True)
    def test_from_env_invalid_api_port(self, mock_exists):
        """Test that from_env raises ValueError when API_PORT is not a valid integer."""
        with self.assertRaises(ValueError) as context:
            WorkerConfig.from_env()

        self.assertIn("API_PORT must be a valid integer", str(context.exception))

    @patch.dict(
        os.environ,
        {
            "HOST_ID": "test-host",
            "DB_SERVICE_URL": "http://localhost:3002",
            "API_HOST": "localhost",
            "API_PORT": "3001",
        },
        clear=True,
    )
    def test_from_env_missing_worker_queue_host(self):
        """Test that from_env raises ValueError when WORKER_QUEUE_HOST is missing."""
        with self.assertRaises(ValueError) as context:
            WorkerConfig.from_env()

        self.assertIn("WORKER_QUEUE_HOST", str(context.exception))

    def test_init_with_explicit_provisioner_path(self):
        """Test initialization with explicit provisioner CLI path."""
        with patch("hlvmp_worker.config.Path.exists", return_value=True):
            config = WorkerConfig(
                database_url="postgresql://localhost/test",
                host_id="test-host",
                api_host="localhost",
                api_port=3001,
                worker_id="test-worker",
                provisioner_cli_path="/test/path",
            )

        repr_str = repr(config)

        self.assertIn("test-host", repr_str)
        self.assertIn("test-worker", repr_str)
        self.assertIn("concurrency=1", repr_str)
        self.assertIn("api_host", repr_str)
        self.assertIn("api_port", repr_str)
        self.assertIn("provisioner_cli_path", repr_str)


if __name__ == "__main__":
    unittest.main()
