"""Unix socket server for worker wakeup and health checks.

Provides a simple socket-based interface for:
- Waking the worker to immediately scan for jobs
- Querying worker health and capacity
"""

import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SocketServer:
    """Unix socket server for worker communication."""

    def __init__(
        self,
        socket_path: str,
        on_wake: Optional[Callable[[], None]] = None,
        on_health: Optional[Callable[[], dict]] = None,
    ):
        """Initialize socket server.

        Args:
            socket_path: Path to Unix socket file
            on_wake: Callback to invoke when wake message is received
            on_health: Callback to invoke when health message is received (returns dict)
        """
        self.socket_path = socket_path
        self.on_wake = on_wake
        self.on_health = on_health
        self.running = False
        self.server_socket: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """Start the socket server in a background thread."""
        if self.running:
            logger.warning("Socket server already running")
            return

        # Remove stale socket file if it exists
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except Exception as e:
                logger.error(f"Failed to remove stale socket file: {e}")
                raise

        # Ensure parent directory exists
        socket_dir = Path(self.socket_path).parent
        socket_dir.mkdir(parents=True, exist_ok=True)

        # Create Unix socket
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(5)

        # Set socket permissions to allow group access
        # Mode 0660: rw-rw----
        try:
            os.chmod(self.socket_path, 0o660)
        except Exception as e:
            logger.warning(f"Failed to set socket permissions: {e}")

        self.running = True
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()

        logger.info(f"Socket server listening on {self.socket_path}")

    def stop(self):
        """Stop the socket server."""
        if not self.running:
            return

        logger.info("Stopping socket server")
        self.running = False

        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception as e:
                logger.warning(f"Error closing server socket: {e}")

        # Wait for thread to finish
        if self.thread:
            self.thread.join(timeout=5.0)

        # Clean up socket file
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except Exception as e:
                logger.warning(f"Failed to remove socket file: {e}")

        logger.info("Socket server stopped")

    def _run_server(self):
        """Run the socket server loop."""
        while self.running:
            try:
                # Accept connection with timeout
                self.server_socket.settimeout(1.0)
                try:
                    client_socket, _ = self.server_socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    # Socket closed
                    break

                # Handle client connection
                self._handle_client(client_socket)

            except Exception as e:
                if self.running:
                    logger.error(f"Error in socket server loop: {e}", exc_info=True)

    def _handle_client(self, client_socket: socket.socket):
        """Handle a client connection.

        Args:
            client_socket: Client socket
        """
        try:
            # Set receive timeout
            client_socket.settimeout(5.0)

            # Receive message (max 1024 bytes)
            data = client_socket.recv(1024)
            if not data:
                return

            message = data.decode("utf-8").strip()
            logger.debug(f"Received message: {message}")

            # Process message
            response = self._process_message(message)

            # Send response
            if response:
                client_socket.sendall(response.encode("utf-8"))

        except socket.timeout:
            logger.warning("Client connection timeout")
        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            client_socket.close()

    def _process_message(self, message: str) -> Optional[str]:
        """Process a received message.

        Args:
            message: Received message

        Returns:
            Response string or None
        """
        if message == "wake":
            logger.info("Received wake message")
            if self.on_wake:
                try:
                    self.on_wake()
                    return "OK\n"
                except Exception as e:
                    logger.error(f"Error invoking wake callback: {e}")
                    return f"ERROR: {e}\n"
            return "OK\n"

        if message == "health":
            logger.debug("Received health message")
            if self.on_health:
                try:
                    health_data = self.on_health()
                    return json.dumps(health_data) + "\n"
                except Exception as e:
                    logger.error(f"Error invoking health callback: {e}")
                    return json.dumps({"status": "error", "error": str(e)}) + "\n"
            return json.dumps({"status": "ok"}) + "\n"

        logger.warning(f"Unknown message: {message}")
        return "ERROR: Unknown message\n"
