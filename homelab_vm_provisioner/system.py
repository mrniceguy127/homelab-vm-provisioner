"""Helpers for invoking host system commands."""

import contextlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .constants import DEFAULT_REQUIRED_TOOLS, INSTALL_HINT

LOCK_ROOT = Path(tempfile.gettempdir()) / "homelab-vm-provisioner" / "locks"


class VmLifecycleLockError(RuntimeError):
    """Raised when another lifecycle operation already holds the host lock."""

    def __init__(self, operation, vm_name=None, lock_path=None, holder=None):
        self.details = {
            "code": "vm_lifecycle_locked",
            "operation": operation,
            "vm_name": vm_name,
            "lock_path": str(lock_path) if lock_path is not None else None,
            "holder": holder or None,
        }

        holder_summary = "another lifecycle operation"
        if holder:
            holder_operation = holder.get("operation")
            holder_vm_name = holder.get("vm_name")
            if holder_operation and holder_vm_name:
                holder_summary = f"{holder_operation} for {holder_vm_name}"
            elif holder_operation:
                holder_summary = holder_operation

        target = f" for {vm_name}" if vm_name else ""
        super().__init__(
            f"Host lifecycle lock is busy while attempting {operation}{target}. "
            f"The lock is currently held by {holder_summary}."
        )


def _lock_holder_details(lock_file):
    """Return JSON metadata stored in an acquired lock file when available."""
    try:
        lock_file.seek(0)
        raw_details = lock_file.read().strip()
    except OSError:
        return None

    if not raw_details:
        return None

    try:
        return json.loads(raw_details)
    except json.JSONDecodeError:
        return {"raw": raw_details}


def tool_exists(tool):
    """Return whether an executable is available on ``PATH``.

    Args:
        tool: Executable name to search for.

    Returns:
        bool: ``True`` when the executable can be found.
    """
    result = subprocess.run(
        ["which", tool],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def run(cmd, sudo=False, check=True):
    """Run a command and echo it before execution.

    Args:
        cmd: Command parts to execute.
        sudo: Prefix the command with ``sudo`` when ``True``.
        check: Raise on non-zero exit status when ``True``.

    Returns:
        subprocess.CompletedProcess: Result from ``subprocess.run``.

    Raises:
        subprocess.CalledProcessError: If ``check`` is ``True`` and the command fails.
    """
    if sudo:
        cmd = ["sudo"] + cmd

    print("+", " ".join(str(x) for x in cmd))
    return subprocess.run(cmd, check=check, text=True)


def capture(cmd, sudo=False):
    """Run a command and return its stripped standard output.

    Args:
        cmd: Command parts to execute.
        sudo: Prefix the command with ``sudo`` when ``True``.

    Returns:
        str: Standard output with surrounding whitespace removed.

    Raises:
        subprocess.CalledProcessError: If the command exits with a non-zero status.
    """
    if sudo:
        cmd = ["sudo"] + cmd

    return subprocess.check_output(cmd, text=True).strip()


def capture_or_none(cmd, sudo=False):
    """Return command output or ``None`` when execution fails.

    Args:
        cmd: Command parts to execute.
        sudo: Prefix the command with ``sudo`` when ``True``.

    Returns:
        str | None: Captured output, or ``None`` when the command cannot be run
        successfully.
    """
    try:
        return capture(cmd, sudo=sudo)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def require_tools(tools=None):
    """Exit the process when required host tools are missing.

    Args:
        tools: Iterable of executable names to validate. Defaults to the core
            provisioning toolchain.

    Raises:
        SystemExit: If any required tool is missing.
    """
    if tools is None:
        tools = DEFAULT_REQUIRED_TOOLS

    missing = [tool for tool in tools if not tool_exists(tool)]
    if not missing:
        return

    print("Missing tools:", ", ".join(missing))
    print("Install with:")
    print(INSTALL_HINT)
    sys.exit(1)


@contextlib.contextmanager
def host_lifecycle_lock(operation, vm_name=None):
    """Hold the shared host-level lifecycle lock for VM mutations.

    Args:
        operation: Human-readable lifecycle operation name.
        vm_name: Optional VM name associated with the operation.

    Raises:
        VmLifecycleLockError: If another process already holds the lock.
    """
    LOCK_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_ROOT / "lifecycle.lock"
    metadata = {
        "operation": operation,
        "vm_name": vm_name,
        "pid": os.getpid(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise VmLifecycleLockError(
                operation,
                vm_name=vm_name,
                lock_path=lock_path,
                holder=_lock_holder_details(lock_file),
            ) from exc

        lock_file.seek(0)
        lock_file.truncate()
        json.dump(metadata, lock_file)
        lock_file.flush()

        try:
            yield
        finally:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.flush()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
