import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import call, patch

from helpers import completed_process

from homelab_vm_provisioner import cli


class BuildNetworkConfigTests(unittest.TestCase):
    def test_builds_nat_auto_network(self):
        with patch.object(
            cli,
            "pick_free_subnet",
            return_value={
                "prefix": "192.168.120",
                "cidr": "192.168.120.0/24",
                "gateway": "192.168.120.1",
                "vm_ip": "192.168.120.50",
                "dhcp_start": "192.168.120.50",
                "dhcp_end": "192.168.120.99",
            },
        ), patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"):
            self.assertEqual(
                cli.build_network_config("demo", {"mode": "nat-auto"}),
                {
                    "mode": "nat-auto",
                    "mac": "52:54:00:aa:bb:cc",
                    "prefix": "192.168.120",
                    "cidr": "192.168.120.0/24",
                    "gateway": "192.168.120.1",
                    "vm_ip": "192.168.120.50",
                    "dhcp_start": "192.168.120.50",
                    "dhcp_end": "192.168.120.99",
                    "name": "demo-net",
                    "zone": "demo-zone",
                },
            )

    def test_builds_bridge_network(self):
        with patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"):
            self.assertEqual(
                cli.build_network_config("demo", {"mode": "bridge", "bridge_name": "br1"}),
                {
                    "mode": "bridge",
                    "mac": "52:54:00:aa:bb:cc",
                    "bridge_name": "br1",
                    "vm_ip": "dhcp-from-router",
                    "cidr": "main-lan",
                },
            )

    def test_raises_for_missing_nat_custom_fields(self):
        with patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"):
            with self.assertRaisesRegex(ValueError, "Missing nat-custom network fields"):
                cli.build_network_config("demo", {"mode": "nat-custom"})

    def test_raises_for_invalid_network_mode(self):
        with patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"):
            with self.assertRaisesRegex(ValueError, "network.mode"):
                cli.build_network_config("demo", {"mode": "invalid"})


class ParserAndMainTests(unittest.TestCase):
    def test_build_parser_parses_subcommands(self):
        parser = cli.build_parser()
        args = parser.parse_args(["destroy", "demo"])

        self.assertEqual(args.command, "destroy")
        self.assertEqual(args.name, "demo")

    def test_main_dispatches_create(self):
        with patch.object(cli, "create") as create_mock:
            cli.main(["create", "configs/demo.yaml"])

        create_mock.assert_called_once_with("configs/demo.yaml")

    def test_main_dispatches_destroy(self):
        with patch.object(cli, "destroy") as destroy_mock:
            cli.main(["destroy", "demo"])

        destroy_mock.assert_called_once_with("demo")

    def test_main_dispatches_ssh_admin(self):
        with patch.object(cli, "ssh_admin") as ssh_admin_mock:
            cli.main(["ssh-admin", "demo", "--ip", "192.168.1.50"])

        ssh_admin_mock.assert_called_once_with("demo", "192.168.1.50")


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

            with patch.object(cli, "require_tools"), patch.object(
                cli,
                "provider_keypair",
                return_value=(provider_key, "ssh-ed25519 AAA provider"),
            ), patch.object(
                cli, "random_mac", return_value="52:54:00:aa:bb:cc"
            ), patch.object(
                cli, "run"
            ), patch.object(
                cli, "ensure_base_image", return_value=Path("/images/base.qcow2")
            ), patch.object(
                cli, "create_vm_disk", return_value=Path("/images/demo.qcow2")
            ), patch.object(
                cli, "create_nat_network"
            ) as create_nat_network_mock, patch.object(
                cli,
                "render_templates",
                return_value=(Path("/build/user-data"), Path("/build/meta-data")),
            ) as render_templates_mock, patch.object(
                cli, "save_vm_state"
            ) as save_state_mock, patch.object(
                cli, "create_seed_iso", return_value=Path("/images/demo-seed.iso")
            ), patch.object(
                cli, "virt_install"
            ) as virt_install_mock, patch.object(
                cli, "apply_firewalld_nat_policy", return_value=False
            ) as firewall_mock:
                cli.create(str(config_path))

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
        self.assertEqual(save_state_mock.call_count, 2)


class SshAdminTests(unittest.TestCase):
    def test_connects_with_resolved_ip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider_key = Path(tmpdir) / "demo_provider_ed25519"
            provider_key.write_text("private", encoding="utf-8")

            with patch.object(cli, "require_tools"), patch.object(
                cli, "vm_exists", return_value=True
            ), patch.object(
                cli, "provider_private_key_path", return_value=provider_key
            ), patch.object(
                cli, "resolve_vm_ipv4", return_value=("192.168.122.50", "agent")
            ), patch.object(
                cli.subprocess, "run", return_value=completed_process(returncode=7)
            ) as run_mock:
                with self.assertRaises(SystemExit) as exc:
                    cli.ssh_admin("demo")

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

            with patch.object(cli, "require_tools"), patch.object(
                cli, "vm_exists", return_value=True
            ), patch.object(
                cli, "provider_private_key_path", return_value=provider_key
            ), patch.object(cli, "resolve_vm_ipv4", return_value=(None, None)):
                with self.assertRaisesRegex(RuntimeError, "Could not determine the VM IP"):
                    cli.ssh_admin("demo")


class DestroyTests(unittest.TestCase):
    def test_destroy_merges_state_and_live_network_then_cleans_everything(self):
        state = {
            "provider_private_key": "/keys/demo_provider_ed25519",
            "network": {
                "name": "state-net",
                "zone": "custom-demo-zone",
                "cidr": "192.168.240.0/24",
                "vm_ip": "192.168.240.50",
            },
            "ports": [{"host": 2222, "guest": 22, "proto": "tcp"}],
        }

        with patch.object(cli, "load_vm_state", return_value=state), patch.object(
            cli,
            "discover_vm_network",
            return_value={"name": "live-net", "vm_ip": "192.168.240.55"},
        ), patch.object(
            cli, "vm_exists", return_value=True
        ), patch.object(
            cli, "cleanup_firewalld_vm_policy"
        ) as cleanup_firewall_mock, patch.object(
            cli, "cleanup_vm_storage"
        ) as cleanup_storage_mock, patch.object(
            cli, "cleanup_local_vm_artifacts"
        ) as cleanup_artifacts_mock, patch.object(cli, "run") as run_mock:
            cli.destroy("demo")

        cleanup_firewall_mock.assert_called_once_with(
            "demo",
            {
                "name": "live-net",
                "zone": "custom-demo-zone",
                "cidr": "192.168.240.0/24",
                "vm_ip": "192.168.240.55",
            },
            [{"host": 2222, "guest": 22, "proto": "tcp"}],
        )
        cleanup_storage_mock.assert_called_once_with("demo")
        cleanup_artifacts_mock.assert_called_once_with(
            "demo",
            provider_private_key="/keys/demo_provider_ed25519",
        )
        self.assertEqual(
            run_mock.call_args_list,
            [
                call(["virsh", "destroy", "demo"], sudo=True, check=False),
                call(
                    ["virsh", "undefine", "demo", "--remove-all-storage"],
                    sudo=True,
                    check=False,
                ),
                call(["virsh", "net-destroy", "live-net"], sudo=True, check=False),
                call(["virsh", "net-undefine", "live-net"], sudo=True, check=False),
            ],
        )
