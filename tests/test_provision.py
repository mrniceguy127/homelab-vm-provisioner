import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from homelab_vm_provisioner import provision


class ProviderKeypairTests(unittest.TestCase):
    def test_generates_missing_keypair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_dir = Path(tmpdir)

            def fake_run(cmd, sudo=False, check=True):
                key_path = Path(cmd[cmd.index("-f") + 1])
                key_path.write_text("private", encoding="utf-8")
                Path(str(key_path) + ".pub").write_text(
                    "ssh-ed25519 AAA provider-demo\n",
                    encoding="utf-8",
                )

            with patch.object(provision, "PROVIDER_KEY_DIR", provider_dir), patch.object(
                provision, "run", side_effect=fake_run
            ) as run_mock:
                key_path, public_key = provision.provider_keypair("demo")

        self.assertEqual(key_path, provider_dir / "demo_provider_ed25519")
        self.assertEqual(public_key, "ssh-ed25519 AAA provider-demo")
        run_mock.assert_called_once()

    def test_reuses_existing_keypair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_dir = Path(tmpdir)
            key_path = provider_dir / "demo_provider_ed25519"
            pub_path = provider_dir / "demo_provider_ed25519.pub"
            provider_dir.mkdir(exist_ok=True)
            key_path.write_text("private", encoding="utf-8")
            pub_path.write_text("ssh-ed25519 AAA existing\n", encoding="utf-8")

            with patch.object(provision, "PROVIDER_KEY_DIR", provider_dir), patch.object(
                provision, "run"
            ) as run_mock:
                returned_key_path, public_key = provision.provider_keypair("demo")

        self.assertEqual(returned_key_path, key_path)
        self.assertEqual(public_key, "ssh-ed25519 AAA existing")
        run_mock.assert_not_called()
