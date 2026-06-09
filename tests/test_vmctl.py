import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import vmctl


def completed_process(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout)


class ParseIpv4FromDomifaddrTests(unittest.TestCase):
    def test_returns_ipv4_address(self):
        output = textwrap.dedent(
            """\
            Name       MAC address          Protocol     Address
            -------------------------------------------------------------------------------
            vnet0      52:54:00:aa:bb:cc    ipv4         192.168.122.50/24
            """
        )

        self.assertEqual(vmctl.parse_ipv4_from_domifaddr(output), "192.168.122.50")

    def test_ignores_non_ipv4_and_invalid_rows(self):
        output = textwrap.dedent(
            """\
            Name       MAC address          Protocol     Address
            -------------------------------------------------------------------------------
            vnet0      52:54:00:aa:bb:cc    ipv6         fd00::50/64
            broken     row
            """
        )

        self.assertIsNone(vmctl.parse_ipv4_from_domifaddr(output))


class ResolveVmIpv4Tests(unittest.TestCase):
    def test_uses_first_source_with_ipv4_address(self):
        output = textwrap.dedent(
            """\
            Name       MAC address          Protocol     Address
            -------------------------------------------------------------------------------
            vnet0      52:54:00:aa:bb:cc    ipv4         192.168.122.77/24
            """
        )

        with patch.object(
            vmctl.subprocess,
            "run",
            side_effect=[
                completed_process(returncode=1),
                completed_process(stdout=output),
            ],
        ) as run_mock:
            self.assertEqual(vmctl.resolve_vm_ipv4("demo"), ("192.168.122.77", "agent"))

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    ["sudo", "virsh", "domifaddr", "demo", "--source", "lease"],
                    stdout=vmctl.subprocess.PIPE,
                    stderr=vmctl.subprocess.DEVNULL,
                    text=True,
                ),
                call(
                    ["sudo", "virsh", "domifaddr", "demo", "--source", "agent"],
                    stdout=vmctl.subprocess.PIPE,
                    stderr=vmctl.subprocess.DEVNULL,
                    text=True,
                ),
            ],
        )

    def test_returns_none_when_no_source_has_ipv4(self):
        with patch.object(
            vmctl.subprocess,
            "run",
            side_effect=[
                completed_process(stdout=""),
                completed_process(stdout=""),
                completed_process(stdout=""),
            ],
        ):
            self.assertEqual(vmctl.resolve_vm_ipv4("demo"), (None, None))


class PickFreeSubnetTests(unittest.TestCase):
    def test_returns_first_available_prefix(self):
        with patch.object(
            vmctl,
            "subnet_appears_used",
            side_effect=[True, True, False],
        ):
            self.assertEqual(
                vmctl.pick_free_subnet(),
                {
                    "prefix": "192.168.102",
                    "cidr": "192.168.102.0/24",
                    "gateway": "192.168.102.1",
                    "vm_ip": "192.168.102.50",
                    "dhcp_start": "192.168.102.50",
                    "dhcp_end": "192.168.102.99",
                },
            )

    def test_raises_when_no_free_subnet_exists(self):
        with patch.object(vmctl, "subnet_appears_used", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Could not find free"):
                vmctl.pick_free_subnet()


class ResolveConfigPathTests(unittest.TestCase):
    def test_accepts_existing_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "demo.yaml"
            config_path.write_text("vm: {}\n", encoding="utf-8")

            self.assertEqual(vmctl.resolve_config_path(str(config_path)), config_path)

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
                resolved = vmctl.resolve_config_path("config/grant-minecraft")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(resolved, Path("configs/grant-minecraft.yaml"))

    def test_raises_for_missing_config(self):
        with self.assertRaisesRegex(FileNotFoundError, "Missing config file"):
            vmctl.resolve_config_path("config/does-not-exist")


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

            with patch.object(vmctl, "PROVIDER_KEY_DIR", provider_dir), patch.object(
                vmctl, "run", side_effect=fake_run
            ) as run_mock:
                key_path, public_key = vmctl.provider_keypair("demo")

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

            with patch.object(vmctl, "PROVIDER_KEY_DIR", provider_dir), patch.object(
                vmctl, "run"
            ) as run_mock:
                returned_key_path, public_key = vmctl.provider_keypair("demo")

        self.assertEqual(returned_key_path, key_path)
        self.assertEqual(public_key, "ssh-ed25519 AAA existing")
        run_mock.assert_not_called()


class CreateTests(unittest.TestCase):
    def test_builds_nat_custom_network_from_subnet_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            tenant_key = tmpdir_path / "tenant.pub"
            tenant_key.write_text("ssh-ed25519 AAA tenant\n", encoding="utf-8")

            config_path = tmpdir_path / "demo.yaml"
            config_path.write_text(
                textwrap.dedent(
                    f"""\
                    vm:
                      name: demo
                      user: tenant
                      ssh_key_file: {tenant_key.as_posix()}
                      ram_mb: 4096
                      vcpus: 2
                      disk_gb: 40
                      allow_sudo: false
                      trust: untrusted
                      template: base

                    network:
                      mode: nat-custom
                      subnet_prefix: 192.168.240

                    packages:
                      - htop

                    ports:
                      - host: 2222
                        guest: 22
                    """
                ),
                encoding="utf-8",
            )

            provider_key = tmpdir_path / "demo_provider_ed25519"

            with patch.object(vmctl, "require_tools"), patch.object(
                vmctl,
                "provider_keypair",
                return_value=(provider_key, "ssh-ed25519 AAA provider"),
            ), patch.object(
                vmctl, "random_mac", return_value="52:54:00:aa:bb:cc"
            ), patch.object(
                vmctl, "run"
            ), patch.object(
                vmctl, "ensure_base_image", return_value=Path("/images/base.qcow2")
            ), patch.object(
                vmctl, "create_vm_disk", return_value=Path("/images/demo.qcow2")
            ), patch.object(
                vmctl, "create_nat_network"
            ) as create_nat_network_mock, patch.object(
                vmctl,
                "render_templates",
                return_value=(Path("/build/user-data"), Path("/build/meta-data")),
            ) as render_templates_mock, patch.object(
                vmctl, "create_seed_iso", return_value=Path("/images/demo-seed.iso")
            ), patch.object(
                vmctl, "virt_install"
            ) as virt_install_mock, patch.object(
                vmctl, "apply_firewalld_nat_policy"
            ) as firewall_mock:
                vmctl.create(str(config_path))

        expected_network = {
            "mode": "nat-custom",
            "mac": "52:54:00:aa:bb:cc",
            "prefix": "192.168.240",
            "cidr": "192.168.240.0/24",
            "gateway": "192.168.240.1",
            "vm_ip": "192.168.240.50",
            "dhcp_start": "192.168.240.50",
            "dhcp_end": "192.168.240.99",
            "name": "demo-net",
            "zone": "demo-zone",
        }

        create_nat_network_mock.assert_called_once_with("demo", expected_network)

        render_context, template_name = render_templates_mock.call_args.args
        self.assertEqual(template_name, "base")
        self.assertEqual(
            render_context,
            {
                "vm_name": "demo",
                "provider_user": "vmadmin",
                "provider_public_key": "ssh-ed25519 AAA provider",
                "vm_user": "tenant",
                "vm_public_key": "ssh-ed25519 AAA tenant",
                "vm_sudo": "false",
                "packages": ["htop"],
            },
        )

        virt_install_mock.assert_called_once_with(
            "demo",
            {
                "name": "demo",
                "user": "tenant",
                "ssh_key_file": tenant_key.as_posix(),
                "ram_mb": 4096,
                "vcpus": 2,
                "disk_gb": 40,
                "allow_sudo": False,
                "trust": "untrusted",
                "template": "base",
            },
            "network=demo-net,model=virtio,mac=52:54:00:aa:bb:cc",
            Path("/images/demo.qcow2"),
            Path("/images/demo-seed.iso"),
        )
        firewall_mock.assert_called_once_with(
            expected_network,
            "untrusted",
            [{"host": 2222, "guest": 22}],
        )


class SshAdminTests(unittest.TestCase):
    def test_connects_with_resolved_ip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_key = Path(tmpdir) / "demo_provider_ed25519"
            provider_key.write_text("private", encoding="utf-8")

            with patch.object(vmctl, "require_tools"), patch.object(
                vmctl, "vm_exists", return_value=True
            ), patch.object(
                vmctl, "provider_private_key_path", return_value=provider_key
            ), patch.object(
                vmctl, "resolve_vm_ipv4", return_value=("192.168.122.50", "agent")
            ), patch.object(
                vmctl.subprocess, "run", return_value=completed_process(returncode=7)
            ) as run_mock:
                with self.assertRaises(SystemExit) as exc:
                    vmctl.ssh_admin("demo")

        self.assertEqual(exc.exception.code, 7)
        run_mock.assert_called_once_with(
            [
                "ssh",
                "-i",
                str(provider_key),
                "-o",
                "IdentitiesOnly=yes",
                "vmadmin@192.168.122.50",
            ]
        )

    def test_raises_when_ip_cannot_be_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_key = Path(tmpdir) / "demo_provider_ed25519"
            provider_key.write_text("private", encoding="utf-8")

            with patch.object(vmctl, "require_tools"), patch.object(
                vmctl, "vm_exists", return_value=True
            ), patch.object(
                vmctl, "provider_private_key_path", return_value=provider_key
            ), patch.object(vmctl, "resolve_vm_ipv4", return_value=(None, None)):
                with self.assertRaisesRegex(RuntimeError, "Could not determine the VM IP"):
                    vmctl.ssh_admin("demo")


if __name__ == "__main__":
    unittest.main()
