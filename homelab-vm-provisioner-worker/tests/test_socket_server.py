"""Tests for socket server module."""

import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hlvmp_worker.socket_server import SocketServer


class TestSocketServer(unittest.TestCase):
    """Test cases for SocketServer."""

    def setUp(self):
        """Set up test fixtures."""
        # Create temporary socket path
        self.temp_dir = tempfile.mkdtemp()
        self.socket_path = os.path.join(self.temp_dir, "test-worker.sock")

        # Track callback invocations
        self.wake_called = 0
        self.health_called = 0

    def tearDown(self):
        """Clean up test fixtures."""
        # Remove temporary directory
        if os.path.exists(self.temp_dir):
            for file in os.listdir(self.temp_dir):
                os.unlink(os.path.join(self.temp_dir, file))
            os.rmdir(self.temp_dir)

    def on_wake(self):
        """Wake callback for testing."""
        self.wake_called += 1

    def on_health(self):
        """Health callback for testing."""
        self.health_called += 1
        return {"status": "ok", "worker_id": "test-worker"}

    def send_message(self, message, timeout=2.0):
        """Send a message to the socket server.

        Args:
            message: Message to send
            timeout: Socket timeout in seconds

        Returns:
            Response string
        """
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(self.socket_path)
            client.sendall((message + "\n").encode("utf-8"))
            response = client.recv(1024).decode("utf-8")
            return response.strip()
        finally:
            client.close()

    def test_start_creates_socket_file(self):
        """Test that starting the server creates the socket file."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)  # Give server time to start

        try:
            self.assertTrue(os.path.exists(self.socket_path))
            # Verify it's a socket
            stat_info = os.stat(self.socket_path)
            self.assertTrue(stat_info.st_mode & 0o170000 == 0o140000)
        finally:
            server.stop()

    def test_stop_removes_socket_file(self):
        """Test that stopping the server removes the socket file."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)
        self.assertTrue(os.path.exists(self.socket_path))

        server.stop()
        self.assertFalse(os.path.exists(self.socket_path))

    def test_wake_message_invokes_callback(self):
        """Test that wake message invokes the wake callback."""
        server = SocketServer(self.socket_path, on_wake=self.on_wake)
        server.start()
        time.sleep(0.1)

        try:
            response = self.send_message("wake")
            self.assertEqual(response, "OK")
            self.assertEqual(self.wake_called, 1)
        finally:
            server.stop()

    def test_health_message_returns_health_data(self):
        """Test that health message returns health data."""
        server = SocketServer(self.socket_path, on_health=self.on_health)
        server.start()
        time.sleep(0.1)

        try:
            response = self.send_message("health")
            data = json.loads(response)
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["worker_id"], "test-worker")
            self.assertEqual(self.health_called, 1)
        finally:
            server.stop()

    def test_wake_without_callback_returns_ok(self):
        """Test that wake message without callback still returns OK."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)

        try:
            response = self.send_message("wake")
            self.assertEqual(response, "OK")
        finally:
            server.stop()

    def test_health_without_callback_returns_ok(self):
        """Test that health message without callback returns basic status."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)

        try:
            response = self.send_message("health")
            data = json.loads(response)
            self.assertEqual(data["status"], "ok")
        finally:
            server.stop()

    def test_unknown_message_returns_error(self):
        """Test that unknown message returns error."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)

        try:
            response = self.send_message("unknown")
            self.assertIn("ERROR", response)
        finally:
            server.stop()

    def test_multiple_wake_messages(self):
        """Test that multiple wake messages work correctly."""
        server = SocketServer(self.socket_path, on_wake=self.on_wake)
        server.start()
        time.sleep(0.1)

        try:
            for _ in range(3):
                response = self.send_message("wake")
                self.assertEqual(response, "OK")

            self.assertEqual(self.wake_called, 3)
        finally:
            server.stop()

    def test_socket_permissions(self):
        """Test that socket has reasonable permissions."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)

        try:
            # Check that socket exists and has rw-rw---- (0660) permissions
            stat_info = os.stat(self.socket_path)
            mode = stat_info.st_mode & 0o777
            # Socket should allow owner and group read/write
            self.assertTrue(mode & 0o660)
        finally:
            server.stop()

    def test_removes_stale_socket_on_start(self):
        """Test that starting removes stale socket file."""
        # Create a stale socket file
        Path(self.socket_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.socket_path).touch()

        self.assertTrue(os.path.exists(self.socket_path))

        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)

        try:
            # Should still exist but be a valid socket now
            self.assertTrue(os.path.exists(self.socket_path))
            # Can connect to it
            response = self.send_message("wake")
            self.assertEqual(response, "OK")
        finally:
            server.stop()

    def test_start_when_already_running(self):
        """Test that starting when already running logs warning."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)

        try:
            # Try to start again - should warn and return
            server.start()
            # Should still be running
            self.assertTrue(server.running)
        finally:
            server.stop()

    def test_stop_when_not_running(self):
        """Test that stopping when not running is safe."""
        server = SocketServer(self.socket_path)
        # Stop without starting - should be safe
        server.stop()
        self.assertFalse(server.running)

    def test_wake_callback_exception(self):
        """Test that wake callback exception is handled."""
        def bad_wake():
            raise RuntimeError("Wake error")

        server = SocketServer(self.socket_path, on_wake=bad_wake)
        server.start()
        time.sleep(0.1)

        try:
            response = self.send_message("wake")
            self.assertIn("ERROR", response)
        finally:
            server.stop()

    def test_health_callback_exception(self):
        """Test that health callback exception is handled."""
        def bad_health():
            raise RuntimeError("Health error")

        server = SocketServer(self.socket_path, on_health=bad_health)
        server.start()
        time.sleep(0.1)

        try:
            response = self.send_message("health")
            data = json.loads(response)
            self.assertEqual(data["status"], "error")
            self.assertIn("Health error", data["error"])
        finally:
            server.stop()

    @patch("os.chmod")
    def test_chmod_failure_is_handled(self, mock_chmod):
        """Test that chmod failure is handled gracefully."""
        mock_chmod.side_effect = OSError("Permission denied")

        server = SocketServer(self.socket_path)
        # Should still start despite chmod failure
        server.start()
        time.sleep(0.1)

        try:
            self.assertTrue(server.running)
            # Should still be able to connect
            response = self.send_message("wake")
            self.assertEqual(response, "OK")
        finally:
            server.stop()

    @patch("os.unlink")
    def test_socket_cleanup_failure(self, mock_unlink):
        """Test that socket cleanup failure during stop is handled."""
        server = SocketServer(self.socket_path)
        server.start()
        time.sleep(0.1)

        # Make unlink fail during cleanup
        mock_unlink.side_effect = OSError("Cannot remove socket")

        # Should still stop gracefully
        server.stop()
        self.assertFalse(server.running)


if __name__ == "__main__":
    unittest.main()
