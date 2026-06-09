import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from homelab_vm_provisioner import config


class ResolveConfigPathTests(unittest.TestCase):
    def test_accepts_existing_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "demo.yaml"
            config_path.write_text("vm: {}\n", encoding="utf-8")

            self.assertEqual(config.resolve_config_path(str(config_path)), config_path)

    def test_expands_config_shorthand_to_configs_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = Path.cwd()
            tmpdir_path = Path(tmpdir)
            configs_dir = tmpdir_path / "configs"
            configs_dir.mkdir()
            config_path = configs_dir / "grant-minecraft.yaml"
            config_path.write_text("vm: {}\n", encoding="utf-8")

            try:
                os.chdir(tmpdir_path)
                resolved = config.resolve_config_path("config/grant-minecraft")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(resolved, Path("configs/grant-minecraft.yaml"))

    def test_raises_for_missing_config(self):
        with self.assertRaisesRegex(FileNotFoundError, "Missing config file"):
            config.resolve_config_path("config/does-not-exist")


class StateFileTests(unittest.TestCase):
    def test_build_dir_and_state_file_follow_vm_name(self):
        with patch.object(config, "BUILD_DIR", Path("/tmp/build-root")):
            self.assertEqual(config.build_dir_for_vm("demo"), Path("/tmp/build-root/demo"))
            self.assertEqual(
                config.state_file_for_vm("demo"),
                Path("/tmp/build-root/demo/state.yaml"),
            )

    def test_save_and_load_vm_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            build_dir = Path(tmpdir)
            state = {"network": {"name": "demo-net"}, "ports": [{"host": 2222}]}

            with patch.object(config, "BUILD_DIR", build_dir):
                config.save_vm_state("demo", state)
                loaded = config.load_vm_state("demo")

        self.assertEqual(loaded, state)

    def test_load_vm_state_returns_empty_dict_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, "BUILD_DIR", Path(tmpdir)):
                self.assertEqual(config.load_vm_state("missing"), {})
