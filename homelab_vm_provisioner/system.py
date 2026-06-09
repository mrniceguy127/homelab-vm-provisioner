"""Helpers for invoking host system commands."""

import subprocess
import sys

from .constants import DEFAULT_REQUIRED_TOOLS, INSTALL_HINT


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
