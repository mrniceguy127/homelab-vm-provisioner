import os
import tempfile
import unittest
from pathlib import Path

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
