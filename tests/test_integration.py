import tempfile
import textwrap
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from helpers import completed_process

from homelab_vm_provisioner import cli, config, firewall, network, provision


class FakeHost:
    def __init__(self):
        self.vm_names = set()
        self.vm_ips = {}
        self.network_xml = {}
        self.zones = {"public"}
        self.zone_sources = {}
        self.zone_rich_rules = {}
        self.forward_ports = {None: set()}
        self.cli_commands = []
        self.provision_commands = []
        self.firewall_commands = []
        self.ssh_commands = []
        self.network_dumpxml_requests = []
        self.domifaddr_requests = []

    def create_nat_network(self, vm_name, network_config):
        xml_text = textwrap.dedent(
            f"""\
            <network>
              <name>{network_config['name']}</name>
              <forward mode='nat'/>
              <bridge name='virbr-demo' stp='on' delay='0'/>
              <ip address='{network_config['gateway']}' netmask='255.255.255.0'>
                <dhcp>
                  <host
                    mac='{network_config['mac']}'
                    name='{vm_name}'
                    ip='{network_config['vm_ip']}'/>
                  <range start='{network_config['dhcp_start']}' end='{network_config['dhcp_end']}'/>
                </dhcp>
              </ip>
            </network>
            """
        ).strip()
        self.network_xml[network_config["name"]] = xml_text
        self.vm_ips[vm_name] = network_config["vm_ip"]

    def cli_run(self, cmd, sudo=False, check=True):
        self.cli_commands.append((list(cmd), sudo, check))

        if cmd[:2] == ["virsh", "destroy"]:
            self.vm_names.discard(cmd[2])
        elif cmd[:2] == ["virsh", "undefine"]:
            self.vm_names.discard(cmd[2])
        elif cmd[:2] == ["virsh", "net-destroy"]:
            self.network_xml.pop(cmd[2], None)
        elif cmd[:2] == ["virsh", "net-undefine"]:
            self.network_xml.pop(cmd[2], None)

        return completed_process()

    def provision_run(self, cmd, sudo=False, check=True):
        self.provision_commands.append((list(cmd), sudo, check))

        if cmd[0] == "ssh-keygen":
            key_path = Path(cmd[cmd.index("-f") + 1])
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text("private", encoding="utf-8")
            Path(str(key_path) + ".pub").write_text(
                f"ssh-ed25519 AAA {key_path.stem}\n",
                encoding="utf-8",
            )
        elif cmd[:2] == ["wget", "-O"]:
            output_path = Path(cmd[2])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("base image", encoding="utf-8")
        elif cmd[:2] == ["qemu-img", "create"]:
            disk_path = Path(cmd[-2])
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text("vm disk", encoding="utf-8")
        elif cmd[0] == "cloud-localds":
            seed_path = Path(cmd[1])
            seed_path.parent.mkdir(parents=True, exist_ok=True)
            seed_path.write_text("seed iso", encoding="utf-8")
        elif cmd[0] == "virt-install":
            self.vm_names.add(cmd[cmd.index("--name") + 1])
        elif cmd[:2] == ["rm", "-f"]:
            artifact_path = Path(cmd[2])
            if artifact_path.exists():
                artifact_path.unlink()

        return completed_process()

    def subprocess_run(self, cmd, stdout=None, stderr=None, text=None, check=None):
        if cmd[:3] == ["sudo", "virsh", "dominfo"]:
            return completed_process(returncode=0 if cmd[3] in self.vm_names else 1)

        if cmd == ["sudo", "virsh", "net-list", "--all", "--name"]:
            names = "\n".join(sorted(self.network_xml))
            if names:
                names += "\n"
            return completed_process(returncode=0, stdout=names)

        if cmd[:3] == ["sudo", "virsh", "domifaddr"]:
            vm_name = cmd[3]
            source = cmd[5]
            self.domifaddr_requests.append((vm_name, source))

            if vm_name not in self.vm_names or vm_name not in self.vm_ips:
                return completed_process(returncode=1, stdout="")

            return completed_process(
                returncode=0,
                stdout=textwrap.dedent(
                    f"""\
                    Name       MAC address          Protocol     Address
                    -------------------------------------------------------------------------------
                    vnet0      52:54:00:aa:bb:cc    ipv4         {self.vm_ips[vm_name]}/24
                    """
                ),
            )

        if cmd and cmd[0] == "ssh":
            self.ssh_commands.append(list(cmd))
            return completed_process(returncode=0)

        return completed_process()

    def firewall_capture(self, cmd, sudo=False):
        return self.firewall_capture_or_none(cmd, sudo=sudo) or ""

    def firewall_capture_or_none(self, cmd, sudo=False):
        zone = self._zone_from_cmd(cmd)

        if cmd[-1] == "--get-zones":
            return " ".join(sorted(self.zones))
        if cmd[-1] == "--list-sources":
            return " ".join(sorted(self.zone_sources.get(zone, set())))
        if cmd[-1] == "--list-forward-ports":
            return " ".join(sorted(self.forward_ports.get(zone, set())))
        if cmd[-1] == "--list-rich-rules":
            return "\n".join(sorted(self.zone_rich_rules.get(zone, set())))
        if cmd[-1].startswith("--list-"):
            return ""

        return None

    def firewall_run(self, cmd, sudo=False, check=True):
        self.firewall_commands.append((list(cmd), sudo, check))
        zone = self._zone_from_cmd(cmd)

        if "--new-zone" in cmd:
            zone = cmd[cmd.index("--new-zone") + 1]
            self.zones.add(zone)
            self.zone_sources.setdefault(zone, set())
            self.zone_rich_rules.setdefault(zone, set())
            self.forward_ports.setdefault(zone, set())
        elif "--delete-zone" in cmd:
            zone = cmd[cmd.index("--delete-zone") + 1]
            self.zones.discard(zone)
            self.zone_sources.pop(zone, None)
            self.zone_rich_rules.pop(zone, None)
            self.forward_ports.pop(zone, None)
        elif "--add-source" in cmd:
            source = cmd[cmd.index("--add-source") + 1]
            self.zone_sources.setdefault(zone, set()).add(source)
        elif "--remove-source" in cmd:
            source = cmd[cmd.index("--remove-source") + 1]
            self.zone_sources.setdefault(zone, set()).discard(source)
        elif "--add-rich-rule" in cmd:
            rule = cmd[cmd.index("--add-rich-rule") + 1]
            self.zone_rich_rules.setdefault(zone, set()).add(rule)
        elif "--remove-rich-rule" in cmd:
            rule = cmd[cmd.index("--remove-rich-rule") + 1]
            self.zone_rich_rules.setdefault(zone, set()).discard(rule)
        else:
            for part in cmd:
                if part.startswith("--add-forward-port="):
                    self.forward_ports.setdefault(zone, set()).add(part.split("=", 1)[1])
                elif part.startswith("--remove-forward-port="):
                    self.forward_ports.setdefault(zone, set()).discard(part.split("=", 1)[1])

        return completed_process()

    def network_capture_or_none(self, cmd, sudo=False):
        if cmd[:2] == ["virsh", "net-dumpxml"]:
            net_name = cmd[2]
            self.network_dumpxml_requests.append(net_name)
            return self.network_xml.get(net_name)

        return None

    @staticmethod
    def _zone_from_cmd(cmd):
        if "--zone" not in cmd:
            return None
        return cmd[cmd.index("--zone") + 1]


class IntegrationTests(unittest.TestCase):
    def write_global_config(self, root_dir, path_overrides=None, image_overrides=None):
        paths = {
            "vm_data_dir": "vm/data",
            "vm_state_dir": "vm/state",
            "user_key_dir": "vm/keys/users",
            "admin_key_dir": "vm/keys/admin",
        }
        if path_overrides:
            paths.update(path_overrides)

        image = {
            "name": "debian-12-generic-amd64.qcow2",
            "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
            "os_variant": "debian12",
        }
        if image_overrides:
            image.update(image_overrides)

        (root_dir / "vmctl.yaml").write_text(
            textwrap.dedent(
                f"""\
                paths:
                  vm_data_dir: {paths['vm_data_dir']}
                  vm_state_dir: {paths['vm_state_dir']}
                  user_key_dir: {paths['user_key_dir']}
                  admin_key_dir: {paths['admin_key_dir']}

                image:
                  name: {image['name']}
                  url: {image['url']}
                  os_variant: {image['os_variant']}
                """
            ),
            encoding="utf-8",
        )

    def write_user_key(self, root_dir, relative_path, key_name="tenant.pub"):
        user_key = root_dir / relative_path / key_name
        user_key.parent.mkdir(parents=True, exist_ok=True)
        user_key.write_text("ssh-ed25519 AAA tenant\n", encoding="utf-8")
        return user_key

    def write_nat_config(
        self,
        root_dir,
        ssh_key_file="tenant.pub",
        vm_data_dir=None,
        image_config=None,
    ):
        config_path = root_dir / "configs" / "demo.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "vm:",
            "  name: demo",
            "  user: tenant",
            "  ram_mb: 4096",
            "  vcpus: 2",
            "  disk_gb: 40",
            "  allow_sudo: false",
            "  trust: untrusted",
            "  template: base",
            "",
            "network:",
            "  mode: nat-custom",
            "  subnet_prefix: 192.168.240",
        ]
        if ssh_key_file is not None:
            lines.insert(3, f"  ssh_key_file: {ssh_key_file}")
        if vm_data_dir is not None:
            lines.extend(("", "paths:", f"  vm_data_dir: {vm_data_dir.as_posix()}"))
        if image_config is not None:
            lines.extend(("", "image:"))
            for field in ("name", "url", "os_variant"):
                if field in image_config:
                    lines.append(f"  {field}: {image_config[field]}")

        lines.extend(
            (
                "",
                "packages:",
                "  - htop",
                "",
                "ports:",
                "  - host: 2222",
                "    guest: 22",
                "  - host: 8080",
                "    guest: 80",
                "    proto: tcp",
            )
        )
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return config_path

    def patch_integration_environment(self, stack, host, root_dir, img_dir):
        stack.enter_context(patch.object(cli, "require_tools"))
        stack.enter_context(patch.object(cli, "random_mac", return_value="52:54:00:aa:bb:cc"))
        stack.enter_context(
            patch.object(cli, "create_nat_network", side_effect=host.create_nat_network)
        )
        stack.enter_context(patch.object(cli, "run", side_effect=host.cli_run))
        stack.enter_context(patch.object(config, "PROJECT_DIR", root_dir))
        stack.enter_context(patch.object(config, "GLOBAL_CONFIG_PATH", root_dir / "vmctl.yaml"))
        stack.enter_context(patch.object(config, "LEGACY_VM_BUILD_DIR", root_dir / ".build"))
        stack.enter_context(patch.object(provision, "IMG_DIR", img_dir))
        stack.enter_context(patch.object(provision, "run", side_effect=host.provision_run))
        stack.enter_context(patch("subprocess.run", side_effect=host.subprocess_run))
        stack.enter_context(patch.object(firewall, "capture", side_effect=host.firewall_capture))
        stack.enter_context(
            patch.object(firewall, "capture_or_none", side_effect=host.firewall_capture_or_none)
        )
        stack.enter_context(patch.object(firewall, "run", side_effect=host.firewall_run))
        stack.enter_context(patch.object(firewall, "tool_exists", return_value=True))
        stack.enter_context(
            patch.object(network, "capture_or_none", side_effect=host.network_capture_or_none)
        )

    def test_global_config_drives_default_vm_data_state_and_key_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            root_dir = Path(tmpdir)
            img_dir = root_dir / "images"
            self.write_global_config(
                root_dir,
                {
                    "vm_data_dir": "custom/vm-data",
                    "vm_state_dir": "custom/vm-state",
                    "user_key_dir": "custom/keys/users",
                    "admin_key_dir": "custom/keys/admin",
                },
                {
                    "name": "ubuntu-24.04.img",
                    "url": "https://example.invalid/images/ubuntu-24.04.img",
                    "os_variant": "ubuntu24.04",
                },
            )
            self.write_user_key(root_dir, Path("custom/keys/users"))
            config_path = self.write_nat_config(root_dir)
            host = FakeHost()

            self.patch_integration_environment(stack, host, root_dir, img_dir)

            cli.main(["create", str(config_path)])

            vm_data_dir = root_dir / "custom" / "vm-data" / "demo"
            state_file = root_dir / "custom" / "vm-state" / "demo.yaml"
            admin_private_key = root_dir / "custom" / "keys" / "admin" / "demo_admin_ed25519"
            state = config.load_vm_state("demo")
            user_data_path = vm_data_dir / "user-data"
            meta_data_path = vm_data_dir / "meta-data"

            self.assertTrue(state_file.exists())
            self.assertEqual(state["config_path"], str(config_path))
            self.assertEqual(state["trust"], "untrusted")
            self.assertEqual(state["vm_data_dir"], str(vm_data_dir))
            self.assertEqual(state["network"]["name"], "demo-net")
            self.assertEqual(state["network"]["vm_ip"], "192.168.240.50")
            self.assertEqual(state["firewalld"], {"zone_created": True})
            self.assertEqual(state["admin_private_key"], str(admin_private_key))
            self.assertTrue(admin_private_key.exists())
            self.assertTrue(Path(str(admin_private_key) + ".pub").exists())
            self.assertTrue(user_data_path.exists())
            self.assertTrue(meta_data_path.exists())
            self.assertTrue((img_dir / "demo.qcow2").exists())
            self.assertTrue((img_dir / "demo-seed.iso").exists())
            self.assertTrue((img_dir / "ubuntu-24.04.img").exists())
            self.assertIn("name: vmadmin", user_data_path.read_text(encoding="utf-8"))
            self.assertIn("ssh-ed25519 AAA tenant", user_data_path.read_text(encoding="utf-8"))
            self.assertIn(
                (
                    [
                        "wget",
                        "-O",
                        str(img_dir / "ubuntu-24.04.img"),
                        "https://example.invalid/images/ubuntu-24.04.img",
                    ],
                    True,
                    True,
                ),
                host.provision_commands,
            )
            self.assertIn(
                (
                    [
                        "virt-install",
                        "--name",
                        "demo",
                        "--memory",
                        "4096",
                        "--vcpus",
                        "2",
                        "--disk",
                        "path=" + str(img_dir / "demo.qcow2") + ",format=qcow2,bus=virtio",
                        "--disk",
                        "path=" + str(img_dir / "demo-seed.iso") + ",device=cdrom",
                        "--os-variant",
                        "ubuntu24.04",
                        "--network",
                        "network=demo-net,model=virtio,mac=52:54:00:aa:bb:cc",
                        "--graphics",
                        "none",
                        "--import",
                        "--noautoconsole",
                    ],
                    True,
                    True,
                ),
                host.provision_commands,
            )
            self.assertEqual(
                host.forward_ports[None],
                {
                    "port=2222:proto=tcp:toaddr=192.168.240.50:toport=22",
                    "port=8080:proto=tcp:toaddr=192.168.240.50:toport=80",
                },
            )
            self.assertIn("demo-zone", host.zones)
            self.assertIn("demo", host.vm_names)

            cli.main(["destroy", "demo"])

            self.assertEqual(host.network_dumpxml_requests, ["demo-net"])
            self.assertFalse(state_file.exists())
            self.assertFalse(admin_private_key.exists())
            self.assertFalse(Path(str(admin_private_key) + ".pub").exists())
            self.assertFalse(vm_data_dir.exists())
            self.assertFalse((img_dir / "demo.qcow2").exists())
            self.assertFalse((img_dir / "demo-seed.iso").exists())
            self.assertNotIn("demo-zone", host.zones)
            self.assertEqual(host.forward_ports[None], set())
            self.assertEqual(host.vm_names, set())
            self.assertEqual(self.extract_virsh_actions(host.cli_commands), [
                ["virsh", "destroy", "demo"],
                ["virsh", "undefine", "demo", "--remove-all-storage"],
                ["virsh", "net-destroy", "demo-net"],
                ["virsh", "net-undefine", "demo-net"],
            ])

    def test_per_vm_data_override_beats_global_default_and_ssh_admin_uses_tracked_key(self):
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            root_dir = Path(tmpdir)
            img_dir = root_dir / "images"
            self.write_global_config(root_dir)
            self.write_user_key(root_dir, Path("vm/keys/users"))
            vm_data_dir = root_dir / "custom-vm-data" / "demo"
            config_path = self.write_nat_config(
                root_dir,
                vm_data_dir=vm_data_dir,
                image_config={
                    "url": "https://example.invalid/images/fedora-40.qcow2",
                    "os_variant": "fedora40",
                },
            )
            host = FakeHost()

            self.patch_integration_environment(stack, host, root_dir, img_dir)

            cli.main(["create", str(config_path)])

            state = config.load_vm_state("demo")
            self.assertEqual(state["vm_data_dir"], str(vm_data_dir))
            self.assertEqual(
                state["admin_private_key"],
                str(root_dir / "vm" / "keys" / "admin" / "demo_admin_ed25519"),
            )
            self.assertIn(
                (
                    [
                        "wget",
                        "-O",
                        str(img_dir / "fedora-40.qcow2"),
                        "https://example.invalid/images/fedora-40.qcow2",
                    ],
                    True,
                    True,
                ),
                host.provision_commands,
            )
            self.assertIn(
                (
                    [
                        "virt-install",
                        "--name",
                        "demo",
                        "--memory",
                        "4096",
                        "--vcpus",
                        "2",
                        "--disk",
                        "path=" + str(img_dir / "demo.qcow2") + ",format=qcow2,bus=virtio",
                        "--disk",
                        "path=" + str(img_dir / "demo-seed.iso") + ",device=cdrom",
                        "--os-variant",
                        "fedora40",
                        "--network",
                        "network=demo-net,model=virtio,mac=52:54:00:aa:bb:cc",
                        "--graphics",
                        "none",
                        "--import",
                        "--noautoconsole",
                    ],
                    True,
                    True,
                ),
                host.provision_commands,
            )

            with self.assertRaises(SystemExit) as exc:
                cli.main(["ssh-admin", "demo"])

            self.assertEqual(exc.exception.code, 0)
            self.assertEqual(host.domifaddr_requests, [("demo", "lease")])
            self.assertEqual(
                host.ssh_commands,
                [[
                    "ssh",
                    "-i",
                    str(root_dir / "vm" / "keys" / "admin" / "demo_admin_ed25519"),
                    "-o",
                    "IdentitiesOnly=yes",
                    "vmadmin@192.168.240.50",
                ]],
            )

    def test_create_without_tenant_key_still_creates_tenant_account(self):
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            root_dir = Path(tmpdir)
            img_dir = root_dir / "images"
            self.write_global_config(root_dir)
            config_path = self.write_nat_config(root_dir, ssh_key_file=None)
            host = FakeHost()

            self.patch_integration_environment(stack, host, root_dir, img_dir)

            cli.main(["create", str(config_path)])

            user_data_path = root_dir / "vm" / "data" / "demo" / "user-data"
            rendered = user_data_path.read_text(encoding="utf-8")

        self.assertIn("- name: tenant", rendered)
        self.assertEqual(rendered.count("ssh_authorized_keys:"), 1)
        self.assertNotIn("ssh-ed25519 AAA tenant", rendered)

    @staticmethod
    def extract_virsh_actions(commands):
        return [cmd for cmd, _, _ in commands if cmd and cmd[0] == "virsh"]
