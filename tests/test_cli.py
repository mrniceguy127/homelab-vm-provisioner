import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import call, patch

from helpers import completed_process

from homelab_vm_provisioner import cli


def printed_output(print_mock):
    return "\n".join(
        " ".join(str(arg) for arg in printed_call.args)
        for printed_call in print_mock.call_args_list
    )


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

    def test_builds_nat_custom_network_from_explicit_fields(self):
        with patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"):
            self.assertEqual(
                cli.build_network_config(
                    "demo",
                    {
                        "mode": "nat-custom",
                        "cidr": "192.168.240.0/24",
                        "gateway": "192.168.240.1",
                        "vm_ip": "192.168.240.60",
                        "dhcp_start": "192.168.240.60",
                        "dhcp_end": "192.168.240.99",
                    },
                ),
                {
                    "mode": "nat-custom",
                    "mac": "52:54:00:aa:bb:cc",
                    "cidr": "192.168.240.0/24",
                    "gateway": "192.168.240.1",
                    "vm_ip": "192.168.240.60",
                    "dhcp_start": "192.168.240.60",
                    "dhcp_end": "192.168.240.99",
                    "name": "demo-net",
                    "zone": "demo-zone",
                },
            )

    def test_raises_for_invalid_nat_custom_subnet_prefix(self):
        with patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"):
            with self.assertRaisesRegex(ValueError, "network.subnet_prefix"):
                cli.build_network_config(
                    "demo",
                    {"mode": "nat-custom", "subnet_prefix": "192.168.300"},
                )

    def test_raises_for_nat_custom_address_outside_cidr(self):
        with patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"):
            with self.assertRaisesRegex(ValueError, "network.gateway must be inside network.cidr"):
                cli.build_network_config(
                    "demo",
                    {
                        "mode": "nat-custom",
                        "cidr": "192.168.240.0/24",
                        "gateway": "192.168.241.1",
                        "vm_ip": "192.168.240.50",
                        "dhcp_start": "192.168.240.50",
                        "dhcp_end": "192.168.240.99",
                    },
                )

    def test_raises_for_invalid_nat_custom_cidr(self):
        with self.assertRaisesRegex(ValueError, "network.cidr"):
            cli._validate_nat_custom_network(
                {
                    "cidr": "not-a-network",
                    "gateway": "192.168.240.1",
                    "vm_ip": "192.168.240.50",
                    "dhcp_start": "192.168.240.50",
                    "dhcp_end": "192.168.240.99",
                }
            )

    def test_raises_for_nat_custom_non_slash_24_cidr(self):
        with self.assertRaisesRegex(ValueError, "network.cidr"):
            cli._validate_nat_custom_network(
                {
                    "cidr": "192.168.240.0/25",
                    "gateway": "192.168.240.1",
                    "vm_ip": "192.168.240.50",
                    "dhcp_start": "192.168.240.50",
                    "dhcp_end": "192.168.240.99",
                }
            )

    def test_raises_for_invalid_nat_custom_ipv4_address(self):
        with self.assertRaisesRegex(ValueError, "network.gateway"):
            cli._validate_nat_custom_network(
                {
                    "cidr": "192.168.240.0/24",
                    "gateway": "bad-address",
                    "vm_ip": "192.168.240.50",
                    "dhcp_start": "192.168.240.50",
                    "dhcp_end": "192.168.240.99",
                }
            )

    def test_raises_for_nat_custom_ipv6_address(self):
        with self.assertRaisesRegex(ValueError, "network.gateway"):
            cli._validate_nat_custom_network(
                {
                    "cidr": "192.168.240.0/24",
                    "gateway": "::1",
                    "vm_ip": "192.168.240.50",
                    "dhcp_start": "192.168.240.50",
                    "dhcp_end": "192.168.240.99",
                }
            )

    def test_raises_when_dhcp_start_is_greater_than_dhcp_end(self):
        with self.assertRaisesRegex(ValueError, "network.dhcp_start must not be greater"):
            cli._validate_nat_custom_network(
                {
                    "cidr": "192.168.240.0/24",
                    "gateway": "192.168.240.1",
                    "vm_ip": "192.168.240.50",
                    "dhcp_start": "192.168.240.99",
                    "dhcp_end": "192.168.240.50",
                }
            )


class VmNameValidationTests(unittest.TestCase):
    def test_accepts_vm_name_at_limit(self):
        self.assertIsNone(cli.validate_vm_name("a" * 12))

    def test_rejects_vm_name_over_limit(self):
        with self.assertRaisesRegex(ValueError, "vm.name must be 12 characters or fewer"):
            cli.validate_vm_name("a" * 13)


class SummaryTests(unittest.TestCase):
    def test_print_create_summary_formats_bridge_access(self):
        with patch("builtins.print") as print_mock:
            admin_key = Path("/keys/demo_admin_ed25519")
            cli.print_create_summary(
                "demo",
                "tenant",
                "trusted",
                {
                    "mode": "bridge",
                    "vm_ip": "dhcp-from-router",
                    "mac": "52:54:00:aa:bb:cc",
                },
                admin_key,
                [],
            )

        printed = printed_output(print_mock)
        self.assertIn("Admin SSH:", printed)
        self.assertIn(f"ssh -i {admin_key} vmadmin@VM_LAN_IP", printed)

    def test_print_create_summary_omits_ssh_sections_when_no_guest_ssh_port_exists(self):
        with patch("builtins.print") as print_mock:
            cli.print_create_summary(
                "demo",
                "tenant",
                "trusted",
                {
                    "mode": "nat-custom",
                    "vm_ip": "192.168.240.50",
                    "mac": "52:54:00:aa:bb:cc",
                },
                Path("/keys/demo_admin_ed25519"),
                [{"host": 8080, "guest": 80}],
            )

        printed = printed_output(print_mock)
        self.assertNotIn("Admin SSH:", printed)
        self.assertNotIn("Tenant SSH:", printed)


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

    def test_main_ignores_unrecognized_command_objects(self):
        fake_parser = type(
            "Parser",
            (),
            {"parse_args": lambda self, argv=None: type("Args", (), {"command": "noop"})()},
        )()

        with patch.object(cli, "build_parser", return_value=fake_parser), patch.object(
            cli, "create"
        ) as create_mock, patch.object(cli, "destroy") as destroy_mock, patch.object(
            cli, "ssh_admin"
        ) as ssh_admin_mock:
            self.assertIsNone(cli.main([]))

        create_mock.assert_not_called()
        destroy_mock.assert_not_called()
        ssh_admin_mock.assert_not_called()


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

            vm_data_dir = tmpdir_path / "vm" / "data" / "demo"
            admin_key_dir = tmpdir_path / "vm" / "keys" / "admin"
            admin_key = admin_key_dir / "demo_admin_ed25519"
            global_config = {
                "paths": {
                    "vm_data_dir": "vm/data",
                    "admin_key_dir": "vm/keys/admin",
                    "user_key_dir": "vm/keys/users",
                    "vm_state_dir": "vm/state",
                }
            }
            image_settings = {
                "name": "ubuntu-24.04.img",
                "url": "https://example.invalid/images/ubuntu-24.04.img",
                "os_variant": "ubuntu24.04",
            }
            dns_settings = {"resolvers": ("1.1.1.1", "1.0.0.1")}

            with patch.object(cli, "require_tools"), patch.object(
                cli, "load_global_config", return_value=global_config
            ), patch.object(
                cli, "vm_data_dir_for_config", return_value=vm_data_dir
            ) as vm_data_dir_mock, patch.object(
                cli, "default_admin_key_dir", return_value=admin_key_dir
            ) as admin_key_dir_mock, patch.object(
                cli,
                "admin_keypair",
                return_value=(admin_key, "ssh-ed25519 AAA admin"),
            ) as admin_keypair_mock, patch.object(
                cli, "random_mac", return_value="52:54:00:aa:bb:cc"
            ), patch.object(
                cli, "run"
            ), patch.object(
                cli, "image_settings_for_config", return_value=image_settings
            ) as image_settings_mock, patch.object(
                cli, "dns_settings_for_config", return_value=dns_settings
            ) as dns_settings_mock, patch.object(
                cli, "validate_os_variant"
            ) as validate_os_variant_mock, patch.object(
                cli, "ensure_base_image", return_value=Path("/images/ubuntu-24.04.img")
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

        vm_data_dir_mock.assert_called_once()
        admin_key_dir_mock.assert_called_once_with(global_config)
        admin_keypair_mock.assert_called_once_with(
            "demo",
            admin_key_dir=admin_key_dir,
        )
        image_settings_mock.assert_called_once_with(
            {
                "vm": {
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
                "network": {
                    "mode": "nat-custom",
                    "subnet_prefix": "192.168.240",
                },
                "packages": ["htop"],
                "ports": [{"host": 2222, "guest": 22}],
            },
            global_config=global_config,
        )
        dns_settings_mock.assert_called_once_with(
            {
                "vm": {
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
                "network": {
                    "mode": "nat-custom",
                    "subnet_prefix": "192.168.240",
                },
                "packages": ["htop"],
                "ports": [{"host": 2222, "guest": 22}],
            },
            global_config=global_config,
        )
        validate_os_variant_mock.assert_called_once_with("ubuntu24.04")
        create_nat_network_mock.assert_called_once_with("demo", expected_network)

        render_context, template_name, render_vm_data_dir = render_templates_mock.call_args.args
        self.assertEqual(template_name, "base")
        self.assertEqual(render_vm_data_dir, vm_data_dir)
        self.assertEqual(
            render_context,
            {
                "vm_name": "demo",
                "admin_user": "vmadmin",
                "admin_public_key": "ssh-ed25519 AAA admin",
                "vm_user": "tenant",
                "vm_public_key": "ssh-ed25519 AAA tenant",
                "vm_sudo": "false",
                "packages": ["htop"],
                "dns_resolvers": ("1.1.1.1", "1.0.0.1"),
                "setup_script_content": None,
            },
        )

        first_state = save_state_mock.call_args_list[0].args[1]
        self.assertEqual(first_state["config_path"], str(config_path))
        self.assertEqual(first_state["vm_data_dir"], str(vm_data_dir))
        self.assertEqual(first_state["admin_private_key"], str(admin_key))

        second_state = save_state_mock.call_args_list[1].args[1]
        self.assertEqual(second_state["firewalld"], {"zone_created": False})

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
            "ubuntu24.04",
        )
        firewall_mock.assert_called_once_with(
            expected_network,
            "untrusted",
            [{"host": 2222, "guest": 22}],
        )
        self.assertEqual(save_state_mock.call_count, 2)

    def test_rejects_invalid_trust_value(self):
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
                      trust: maybe
                    """
                ),
                encoding="utf-8",
            )

            with patch.object(cli, "require_tools"), patch.object(
                cli, "load_global_config", return_value={}
            ):
                with self.assertRaisesRegex(ValueError, "vm.trust"):
                    cli.create(str(config_path))

    def test_rejects_vm_name_that_is_too_long(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            tenant_key = tmpdir_path / "tenant.pub"
            tenant_key.write_text("ssh-ed25519 AAA tenant\n", encoding="utf-8")
            config_path = tmpdir_path / "demo.yaml"
            config_path.write_text(
                textwrap.dedent(
                    f"""\
                    vm:
                      name: grant-minecraft
                      user: tenant
                      ssh_key_file: {tenant_key.as_posix()}
                      ram_mb: 4096
                      vcpus: 2
                      disk_gb: 40
                    """
                ),
                encoding="utf-8",
            )

            with patch.object(cli, "require_tools"), patch.object(
                cli, "load_global_config", return_value={}
            ), patch.object(cli, "save_vm_state") as save_vm_state_mock:
                with self.assertRaisesRegex(ValueError, "vm.name must be 12 characters or fewer"):
                    cli.create(str(config_path))

        save_vm_state_mock.assert_not_called()

    def test_raises_when_vm_ssh_key_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "demo.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """\
                    vm:
                      name: demo
                      user: tenant
                      ssh_key_file: missing.pub
                      ram_mb: 4096
                      vcpus: 2
                      disk_gb: 40
                    """
                ),
                encoding="utf-8",
            )

            with patch.object(cli, "require_tools"), patch.object(
                cli, "load_global_config", return_value={}
            ), patch.object(
                cli, "resolve_user_key_path", return_value=Path(tmpdir) / "missing.pub"
            ):
                with self.assertRaisesRegex(FileNotFoundError, "Missing VM SSH key file"):
                    cli.create(str(config_path))

    def test_raises_early_when_os_variant_is_unsupported(self):
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
                    """
                ),
                encoding="utf-8",
            )

            with patch.object(cli, "require_tools"), patch.object(
                cli, "load_global_config", return_value={}
            ), patch.object(
                cli, "image_settings_for_config", return_value={"os_variant": "debian12"}
            ), patch.object(
                cli, "validate_os_variant", side_effect=ValueError("bad os variant")
            ), patch.object(cli, "save_vm_state") as save_vm_state_mock:
                with self.assertRaisesRegex(ValueError, "bad os variant"):
                    cli.create(str(config_path))

        save_vm_state_mock.assert_not_called()

    def test_allows_vm_creation_without_tenant_ssh_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            config_path = tmpdir_path / "demo.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """\
                    vm:
                      name: demo
                      user: tenant
                      ram_mb: 4096
                      vcpus: 2
                      disk_gb: 40

                    network:
                      mode: nat-custom
                      subnet_prefix: 192.168.240
                    """
                ),
                encoding="utf-8",
            )

            vm_data_dir = tmpdir_path / "vm" / "data" / "demo"
            admin_key_dir = tmpdir_path / "vm" / "keys" / "admin"
            admin_key = admin_key_dir / "demo_admin_ed25519"
            image_settings = {
                "name": "ubuntu-24.04.img",
                "url": "https://example.invalid/images/ubuntu-24.04.img",
                "os_variant": "ubuntu24.04",
            }
            dns_settings = {"resolvers": ("9.9.9.9", "149.112.112.112")}

            with patch.object(cli, "require_tools"), patch.object(
                cli, "load_global_config", return_value={}
            ), patch.object(
                cli, "resolve_user_key_path"
            ) as resolve_user_key_path_mock, patch.object(
                cli, "vm_data_dir_for_config", return_value=vm_data_dir
            ), patch.object(
                cli, "default_admin_key_dir", return_value=admin_key_dir
            ), patch.object(
                cli, "admin_keypair", return_value=(admin_key, "ssh-ed25519 AAA admin")
            ), patch.object(
                cli, "random_mac", return_value="52:54:00:aa:bb:cc"
            ), patch.object(
                cli, "run"
            ), patch.object(
                cli, "image_settings_for_config", return_value=image_settings
            ), patch.object(
                cli, "dns_settings_for_config", return_value=dns_settings
            ), patch.object(
                cli, "validate_os_variant"
            ), patch.object(
                cli, "ensure_base_image", return_value=Path("/images/ubuntu-24.04.img")
            ), patch.object(
                cli, "create_vm_disk", return_value=Path("/images/demo.qcow2")
            ), patch.object(
                cli, "create_nat_network"
            ), patch.object(
                cli,
                "render_templates",
                return_value=(Path("/build/user-data"), Path("/build/meta-data")),
            ) as render_templates_mock, patch.object(
                cli, "save_vm_state"
            ) as save_state_mock, patch.object(
                cli, "create_seed_iso", return_value=Path("/images/demo-seed.iso")
            ), patch.object(
                cli, "virt_install"
            ), patch.object(
                cli, "apply_firewalld_nat_policy", return_value=False
            ):
                cli.create(str(config_path))

        resolve_user_key_path_mock.assert_not_called()
        render_context, _, _ = render_templates_mock.call_args.args
        self.assertIsNone(render_context["vm_public_key"])
        self.assertEqual(render_context["dns_resolvers"], ("9.9.9.9", "149.112.112.112"))
        first_state = save_state_mock.call_args_list[0].args[1]
        self.assertEqual(first_state["admin_private_key"], str(admin_key))

    def test_bridge_mode_skips_nat_firewall_and_uses_bridge_network_arg(self):
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

                    network:
                      mode: bridge
                      bridge_name: br1
                    """
                ),
                encoding="utf-8",
            )

            vm_data_dir = tmpdir_path / "vm" / "data" / "demo"
            admin_key = tmpdir_path / "vm" / "keys" / "admin" / "demo_admin_ed25519"
            image_settings = {
                "name": "ubuntu-24.04.img",
                "url": "https://example.invalid/images/ubuntu-24.04.img",
                "os_variant": "ubuntu24.04",
            }
            dns_settings = {"resolvers": ("1.1.1.1", "1.0.0.1")}

            with patch.object(cli, "require_tools"), patch.object(
                cli, "load_global_config", return_value={}
            ), patch.object(
                cli, "vm_data_dir_for_config", return_value=vm_data_dir
            ), patch.object(
                cli, "default_admin_key_dir", return_value=tmpdir_path / "vm" / "keys" / "admin"
            ), patch.object(
                cli, "admin_keypair", return_value=(admin_key, "ssh-ed25519 AAA admin")
            ), patch.object(
                cli, "random_mac", return_value="52:54:00:aa:bb:cc"
            ), patch.object(
                cli, "run"
            ), patch.object(
                cli, "image_settings_for_config", return_value=image_settings
            ), patch.object(
                cli, "dns_settings_for_config", return_value=dns_settings
            ), patch.object(
                cli, "validate_os_variant"
            ), patch.object(
                cli, "ensure_base_image", return_value=Path("/images/ubuntu-24.04.img")
            ), patch.object(
                cli, "create_vm_disk", return_value=Path("/images/demo.qcow2")
            ), patch.object(
                cli,
                "render_templates",
                return_value=(Path("/build/user-data"), Path("/build/meta-data")),
            ), patch.object(
                cli, "save_vm_state"
            ) as save_state_mock, patch.object(
                cli, "create_seed_iso", return_value=Path("/images/demo-seed.iso")
            ), patch.object(
                cli, "virt_install"
            ) as virt_install_mock, patch.object(
                cli, "apply_firewalld_nat_policy"
            ) as firewall_mock, patch("builtins.print") as print_mock:
                cli.create(str(config_path))

        firewall_mock.assert_not_called()
        self.assertEqual(save_state_mock.call_count, 1)
        virt_install_mock.assert_called_once_with(
            "demo",
            {
                "name": "demo",
                "user": "tenant",
                "ssh_key_file": tenant_key.as_posix(),
                "ram_mb": 4096,
                "vcpus": 2,
                "disk_gb": 40,
            },
            "bridge=br1,model=virtio,mac=52:54:00:aa:bb:cc",
            Path("/images/demo.qcow2"),
            Path("/images/demo-seed.iso"),
            "ubuntu24.04",
        )
        printed = printed_output(print_mock)
        self.assertIn("Bridge mode selected", printed)


class SshAdminTests(unittest.TestCase):
    def test_raises_when_vm_is_missing(self):
        with patch.object(cli, "require_tools"), patch.object(cli, "vm_exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "VM not found"):
                cli.ssh_admin("demo")

    def test_connects_with_resolved_ip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_key = Path(tmpdir) / "admin_ed25519"
            admin_key.write_text("private", encoding="utf-8")

            with patch.object(cli, "require_tools"), patch.object(
                cli, "vm_exists", return_value=True
            ), patch.object(
                cli, "load_vm_state", return_value={"admin_private_key": str(admin_key)}
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
                str(admin_key),
                "-o",
                "IdentitiesOnly=yes",
                "vmadmin@192.168.122.50",
            ]
        )

    def test_raises_when_ip_cannot_be_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_key = Path(tmpdir) / "admin_ed25519"
            admin_key.write_text("private", encoding="utf-8")

            with patch.object(cli, "require_tools"), patch.object(
                cli, "vm_exists", return_value=True
            ), patch.object(
                cli, "load_vm_state", return_value={"admin_private_key": str(admin_key)}
            ), patch.object(cli, "resolve_vm_ipv4", return_value=(None, None)):
                with self.assertRaisesRegex(RuntimeError, "Could not determine the VM IP"):
                    cli.ssh_admin("demo")

    def test_falls_back_to_default_admin_key_dir_when_state_has_no_key_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_key_dir = Path(tmpdir) / "vm" / "keys" / "admin"
            admin_key = admin_key_dir / "demo_admin_ed25519"
            admin_key_dir.mkdir(parents=True)
            admin_key.write_text("private", encoding="utf-8")

            with patch.object(cli, "require_tools"), patch.object(
                cli, "vm_exists", return_value=True
            ), patch.object(cli, "load_global_config", return_value={}), patch.object(
                cli, "load_vm_state", return_value={}
            ), patch.object(
                cli, "default_admin_key_dir", return_value=admin_key_dir
            ) as default_admin_key_dir_mock, patch.object(
                cli, "resolve_vm_ipv4", return_value=("192.168.122.50", "agent")
            ), patch.object(
                cli.subprocess, "run", return_value=completed_process(returncode=0)
            ):
                with self.assertRaises(SystemExit) as exc:
                    cli.ssh_admin("demo")

        self.assertEqual(exc.exception.code, 0)
        default_admin_key_dir_mock.assert_called_once()

    def test_uses_provided_ip_without_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_key = Path(tmpdir) / "demo_admin_ed25519"
            admin_key.write_text("private", encoding="utf-8")

            with patch.object(cli, "require_tools"), patch.object(
                cli, "vm_exists", return_value=True
            ), patch.object(
                cli, "load_vm_state", return_value={"admin_private_key": str(admin_key)}
            ), patch.object(cli, "resolve_vm_ipv4") as resolve_vm_ipv4_mock, patch.object(
                cli.subprocess, "run", return_value=completed_process(returncode=0)
            ) as run_mock:
                with self.assertRaises(SystemExit) as exc:
                    cli.ssh_admin("demo", vm_ip="192.168.1.50")

        self.assertEqual(exc.exception.code, 0)
        resolve_vm_ipv4_mock.assert_not_called()
        run_mock.assert_called_once_with(
            [
                "ssh",
                "-i",
                str(admin_key),
                "-o",
                "IdentitiesOnly=yes",
                "vmadmin@192.168.1.50",
            ]
        )

    def test_raises_when_admin_key_is_missing(self):
        missing_key = Path("/missing/demo_admin_ed25519")

        with patch.object(cli, "require_tools"), patch.object(
            cli, "vm_exists", return_value=True
        ), patch.object(
            cli, "load_vm_state", return_value={"admin_private_key": str(missing_key)}
        ):
            with self.assertRaisesRegex(FileNotFoundError, "Missing admin SSH key"):
                cli.ssh_admin("demo")


class DestroyTests(unittest.TestCase):
    def test_destroy_merges_state_and_live_network_then_cleans_everything(self):
        state = {
            "admin_private_key": "/vm/keys/admin/demo_admin_ed25519",
            "vm_data_dir": "/vm/data/demo",
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
            cli, "bridge_interface_exists", side_effect=[True, True]
        ) as bridge_exists_mock, patch.object(
            cli, "cleanup_bridge_interface"
        ) as cleanup_bridge_mock, patch.object(
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
        self.assertEqual(bridge_exists_mock.call_count, 2)
        cleanup_bridge_mock.assert_has_calls(
            [call(cli.default_nat_bridge_name("demo")), call(cli.legacy_nat_bridge_name("demo"))],
            any_order=True,
        )
        cleanup_artifacts_mock.assert_called_once_with(
            "demo",
            admin_private_key="/vm/keys/admin/demo_admin_ed25519",
            vm_data_dir="/vm/data/demo",
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

    def test_destroy_skips_domain_teardown_when_vm_is_missing(self):
        with patch.object(cli, "load_vm_state", return_value={}), patch.object(
            cli, "discover_vm_network", return_value={}
        ), patch.object(cli, "vm_exists", return_value=False), patch.object(
            cli, "bridge_interface_exists", side_effect=[False, False]
        ), patch.object(
            cli, "cleanup_bridge_interface"
        ) as cleanup_bridge_mock, patch.object(
            cli, "cleanup_firewalld_vm_policy"
        ), patch.object(cli, "cleanup_vm_storage"), patch.object(
            cli, "cleanup_local_vm_artifacts"
        ), patch.object(cli, "run") as run_mock:
            cli.destroy("demo")

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(["virsh", "net-destroy", "demo-net"], sudo=True, check=False),
                call(["virsh", "net-undefine", "demo-net"], sudo=True, check=False),
            ],
        )
        cleanup_bridge_mock.assert_not_called()
