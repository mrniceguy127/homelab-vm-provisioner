import subprocess
import unittest
from unittest.mock import patch

from helpers import completed_process

from homelab_vm_provisioner import system


class ToolExistsTests(unittest.TestCase):
    def test_returns_true_when_command_exists(self):
        with patch.object(system.subprocess, "run", return_value=completed_process(returncode=0)):
            self.assertTrue(system.tool_exists("virsh"))

    def test_returns_false_when_command_is_missing(self):
        with patch.object(system.subprocess, "run", return_value=completed_process(returncode=1)):
            self.assertFalse(system.tool_exists("virsh"))


class RunTests(unittest.TestCase):
    def test_runs_plain_command(self):
        with patch.object(system.subprocess, "run", return_value=completed_process()) as run_mock:
            result = system.run(["virsh", "list"])

        self.assertEqual(result.returncode, 0)
        run_mock.assert_called_once_with(["virsh", "list"], check=True, text=True)

    def test_runs_sudo_command(self):
        with patch.object(system.subprocess, "run", return_value=completed_process()) as run_mock:
            system.run(["virsh", "list"], sudo=True, check=False)

        run_mock.assert_called_once_with(["sudo", "virsh", "list"], check=False, text=True)


class CaptureTests(unittest.TestCase):
    def test_returns_stripped_output(self):
        with patch.object(
            system.subprocess,
            "check_output",
            return_value=" value\n",
        ) as output_mock:
            self.assertEqual(system.capture(["virsh", "list"]), "value")

        output_mock.assert_called_once_with(["virsh", "list"], text=True)

    def test_prefixes_sudo_when_requested(self):
        with patch.object(system.subprocess, "check_output", return_value="value") as output_mock:
            system.capture(["virsh", "list"], sudo=True)

        output_mock.assert_called_once_with(["sudo", "virsh", "list"], text=True)


class CaptureOrNoneTests(unittest.TestCase):
    def test_returns_output_on_success(self):
        with patch.object(system, "capture", return_value="value"):
            self.assertEqual(system.capture_or_none(["virsh"]), "value")

    def test_returns_none_on_called_process_error(self):
        with patch.object(
            system,
            "capture",
            side_effect=subprocess.CalledProcessError(1, ["virsh"]),
        ):
            self.assertIsNone(system.capture_or_none(["virsh"]))

    def test_returns_none_on_missing_command(self):
        with patch.object(system, "capture", side_effect=FileNotFoundError):
            self.assertIsNone(system.capture_or_none(["virsh"]))


class RequireToolsTests(unittest.TestCase):
    def test_returns_when_all_tools_exist(self):
        with patch.object(system, "tool_exists", return_value=True):
            self.assertIsNone(system.require_tools(["virsh", "wget"]))

    def test_exits_when_any_tool_is_missing(self):
        with patch.object(system, "tool_exists", side_effect=[True, False]):
            with self.assertRaises(SystemExit) as exc:
                system.require_tools(["virsh", "wget"])

        self.assertEqual(exc.exception.code, 1)
