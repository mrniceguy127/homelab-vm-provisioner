import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from homelab_vm_provisioner import provision


class AdminKeypairTests(unittest.TestCase):
    def test_generates_missing_keypair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_dir = Path(tmpdir)

            def fake_run(cmd, sudo=False, check=True):
                key_path = Path(cmd[cmd.index("-f") + 1])
                key_path.write_text("private", encoding="utf-8")
                Path(str(key_path) + ".pub").write_text(
                    "ssh-ed25519 AAA admin-demo\n",
                    encoding="utf-8",
                )

            with patch.object(provision, "run", side_effect=fake_run) as run_mock:
                key_path, public_key = provision.admin_keypair(
                    "demo", admin_key_dir=admin_dir
                )

        self.assertEqual(key_path, admin_dir / "demo_admin_ed25519")
        self.assertEqual(public_key, "ssh-ed25519 AAA admin-demo")
        run_mock.assert_called_once()

    def test_admin_private_key_path_uses_default_admin_dir(self):
        with patch.object(provision, "default_admin_key_dir", return_value=Path("/keys/admin")):
            self.assertEqual(
                provision.admin_private_key_path("demo"),
                Path("/keys/admin/demo_admin_ed25519"),
            )

    def test_reuses_existing_keypair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_dir = Path(tmpdir)
            key_path = admin_dir / "demo_admin_ed25519"
            pub_path = admin_dir / "demo_admin_ed25519.pub"
            admin_dir.mkdir(exist_ok=True)
            key_path.write_text("private", encoding="utf-8")
            pub_path.write_text("ssh-ed25519 AAA existing\n", encoding="utf-8")

            with patch.object(provision, "run") as run_mock:
                returned_key_path, public_key = provision.admin_keypair(
                    "demo", admin_key_dir=admin_dir
                )

        self.assertEqual(returned_key_path, key_path)
        self.assertEqual(public_key, "ssh-ed25519 AAA existing")
        run_mock.assert_not_called()


class TemplateRenderTests(unittest.TestCase):
    def test_render_templates_writes_cloud_init_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vm_data_dir = Path(tmpdir) / "demo"

            user_data, meta_data = provision.render_templates(
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
                "base",
                vm_data_dir,
            )

            rendered = user_data.read_text(encoding="utf-8")
            self.assertTrue(user_data.exists())
            self.assertTrue(meta_data.exists())
            self.assertIn("tenant", rendered)
            self.assertIn("manage_resolv_conf: true", rendered)
            self.assertIn("- 1.1.1.1", rendered)
            self.assertIn("- 1.0.0.1", rendered)
            self.assertIn("path: /usr/local/share/vm-resolv.conf", rendered)
            self.assertIn("path: /usr/local/sbin/vm-package-setup", rendered)
            self.assertIn("apt-get install -y $packages", rendered)
            self.assertIn("dnf -y install $packages", rendered)
            self.assertIn("pacman -S --noconfirm --needed $packages", rendered)
            self.assertIn("/usr/local/sbin/vm-package-setup", rendered)
            self.assertIn("nameserver 1.1.1.1", rendered)
            self.assertIn("nameserver 1.0.0.1", rendered)
            self.assertIn("cp /usr/local/share/vm-resolv.conf /etc/resolv.conf", rendered)
            package_setup_section = rendered.split("/usr/local/sbin/vm-package-setup\n", 1)[1]
            self.assertLess(
                package_setup_section.index("cp /usr/local/share/vm-resolv.conf /etc/resolv.conf"),
                package_setup_section.index("apt-get update"),
            )
            self.assertNotIn("package_update:", rendered)
            self.assertNotIn("package_upgrade:", rendered)
            self.assertIn("instance-id: demo", meta_data.read_text(encoding="utf-8"))

    def test_render_templates_skips_tenant_authorized_keys_when_key_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vm_data_dir = Path(tmpdir) / "demo"

            user_data, _ = provision.render_templates(
                {
                    "vm_name": "demo",
                    "admin_user": "vmadmin",
                    "admin_public_key": "ssh-ed25519 AAA admin",
                    "vm_user": "tenant",
                    "vm_public_key": None,
                    "vm_sudo": "false",
                    "packages": [],
                    "dns_resolvers": ("1.1.1.1", "1.0.0.1"),
                    "setup_script_content": None,
                },
                "base",
                vm_data_dir,
            )

            rendered = user_data.read_text(encoding="utf-8")

        self.assertIn("- name: tenant", rendered)
        self.assertEqual(rendered.count("ssh_authorized_keys:"), 1)
        self.assertIn("- 1.1.1.1", rendered)
        self.assertIn("- 1.0.0.1", rendered)
        self.assertIn("nameserver 1.1.1.1", rendered)
        self.assertIn("nameserver 1.0.0.1", rendered)
        self.assertIn("cp /usr/local/share/vm-resolv.conf /etc/resolv.conf", rendered)
        self.assertIn("/usr/local/sbin/vm-package-setup", rendered)


class ImageAndDiskTests(unittest.TestCase):
    def test_ensure_base_image_downloads_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img_dir = Path(tmpdir)
            image_settings = {
                "name": "ubuntu-24.04.img",
                "url": "https://example.invalid/images/ubuntu-24.04.img",
                "os_variant": "ubuntu24.04",
            }
            with patch.object(provision, "IMG_DIR", img_dir), patch.object(
                provision, "run"
            ) as run_mock:
                base_img = provision.ensure_base_image(image_settings)

        self.assertEqual(base_img, img_dir / "ubuntu-24.04.img")
        run_mock.assert_called_once_with(
            [
                "wget",
                "-O",
                str(img_dir / "ubuntu-24.04.img"),
                "https://example.invalid/images/ubuntu-24.04.img",
            ],
            sudo=True,
        )

    def test_ensure_base_image_reuses_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img_dir = Path(tmpdir)
            base_img = img_dir / "ubuntu-24.04.img"
            img_dir.mkdir(exist_ok=True)
            base_img.write_text("image", encoding="utf-8")
            image_settings = {
                "name": "ubuntu-24.04.img",
                "url": "https://example.invalid/images/ubuntu-24.04.img",
                "os_variant": "ubuntu24.04",
            }

            with patch.object(provision, "IMG_DIR", img_dir), patch.object(
                provision, "run"
            ) as run_mock:
                resolved = provision.ensure_base_image(image_settings)

        self.assertEqual(resolved, base_img)
        run_mock.assert_not_called()

    def test_create_vm_disk_runs_qemu_img_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img_dir = Path(tmpdir)
            base_img = img_dir / "base.qcow2"
            base_img.write_text("base", encoding="utf-8")

            with patch.object(provision, "IMG_DIR", img_dir), patch.object(
                provision, "run"
            ) as run_mock:
                vm_disk = provision.create_vm_disk("demo", 40, base_img)

        self.assertEqual(vm_disk, img_dir / "demo.qcow2")
        run_mock.assert_called_once_with(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(base_img),
                str(img_dir / "demo.qcow2"),
                "40G",
            ],
            sudo=True,
        )

    def test_create_vm_disk_reuses_existing_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img_dir = Path(tmpdir)
            base_img = img_dir / "base.qcow2"
            base_img.write_text("base", encoding="utf-8")
            vm_disk = img_dir / "demo.qcow2"
            vm_disk.write_text("disk", encoding="utf-8")

            with patch.object(provision, "IMG_DIR", img_dir), patch.object(
                provision, "run"
            ) as run_mock:
                resolved = provision.create_vm_disk("demo", 40, base_img)

        self.assertEqual(resolved, vm_disk)
        run_mock.assert_not_called()

    def test_create_seed_iso_runs_cloud_localds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img_dir = Path(tmpdir)
            user_data = img_dir / "user-data"
            meta_data = img_dir / "meta-data"
            user_data.write_text("user", encoding="utf-8")
            meta_data.write_text("meta", encoding="utf-8")

            with patch.object(provision, "IMG_DIR", img_dir), patch.object(
                provision, "run"
            ) as run_mock:
                seed_iso = provision.create_seed_iso("demo", user_data, meta_data)

        self.assertEqual(seed_iso, img_dir / "demo-seed.iso")
        run_mock.assert_called_once_with(
            ["cloud-localds", str(img_dir / "demo-seed.iso"), str(user_data), str(meta_data)],
            sudo=True,
        )


class LibvirtProvisionTests(unittest.TestCase):
    def test_default_nat_bridge_name_is_stable_and_short(self):
        bridge_name = provision.default_nat_bridge_name("grant-minecraft")
        self.assertEqual(bridge_name, provision.default_nat_bridge_name("grant-minecraft"))
        self.assertTrue(bridge_name.startswith("virbr-"))
        self.assertLessEqual(len(bridge_name), 15)

    def test_legacy_nat_bridge_name_uses_truncated_vm_name(self):
        self.assertEqual(provision.legacy_nat_bridge_name("grant-minecraft"), "virbr-grant-")

    def test_bridge_interface_exists_reflects_subprocess_status(self):
        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0})(),
        ):
            self.assertTrue(provision.bridge_interface_exists("virbr-demo"))

        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 1})(),
        ):
            self.assertFalse(provision.bridge_interface_exists("virbr-demo"))

    def test_bridge_interface_exists_returns_false_when_ip_is_missing(self):
        with patch.object(provision.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(provision.bridge_interface_exists("virbr-demo"))

    def test_cleanup_bridge_interface_uses_ip_link_delete(self):
        with patch.object(provision, "run") as run_mock:
            provision.cleanup_bridge_interface("virbr-demo")

        run_mock.assert_called_once_with(
            ["ip", "link", "delete", "virbr-demo", "type", "bridge"],
            sudo=True,
            check=False,
        )

    def test_os_variant_supported_returns_true_when_variant_is_listed(self):
        output = "short-id | name\ngeneric | Generic\nubuntu24.04 | Ubuntu 24.04\n"

        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0, "stdout": output})(),
        ):
            self.assertTrue(provision.os_variant_supported("ubuntu24.04"))

    def test_os_variant_supported_returns_false_when_variant_is_missing(self):
        output = "short-id | name\ngeneric | Generic\nubuntu24.04 | Ubuntu 24.04\n"

        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0, "stdout": output})(),
        ):
            self.assertFalse(provision.os_variant_supported("debian12"))

    def test_os_variant_supported_skips_validation_when_query_fails(self):
        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 1, "stdout": ""})(),
        ):
            self.assertTrue(provision.os_variant_supported("anything"))

    def test_os_variant_supported_skips_validation_when_virt_install_is_missing(self):
        with patch.object(provision.subprocess, "run", side_effect=FileNotFoundError):
            self.assertTrue(provision.os_variant_supported("anything"))

    def test_os_variant_supported_skips_validation_when_output_is_unparseable(self):
        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0, "stdout": "no table output"})(),
        ):
            self.assertTrue(provision.os_variant_supported("anything"))

    def test_validate_os_variant_raises_for_unsupported_value(self):
        with patch.object(provision, "os_variant_supported", return_value=False):
            with self.assertRaisesRegex(ValueError, "image.os_variant 'debian12'"):
                provision.validate_os_variant("debian12")

    def test_create_nat_network_defines_network_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_xml_path = Path(tmpdir) / "demo-net.xml"

            def fake_path(path_str):
                if path_str == "/tmp":
                    return Path(tmpdir)
                return Path(path_str)

            with patch.object(provision, "Path", side_effect=fake_path), patch.object(
                provision.subprocess,
                "run",
                return_value=type("Result", (), {"returncode": 1})(),
            ), patch.object(provision, "run") as run_mock:
                provision.create_nat_network(
                    "demo",
                    {
                        "name": "demo-net",
                        "gateway": "192.168.240.1",
                        "mac": "52:54:00:aa:bb:cc",
                        "vm_ip": "192.168.240.50",
                        "dhcp_start": "192.168.240.50",
                        "dhcp_end": "192.168.240.99",
                    },
                )

            self.assertTrue(fake_xml_path.exists())
            xml_text = fake_xml_path.read_text(encoding="utf-8")
            self.assertNotIn("<dns>", xml_text)
            self.assertEqual(
                run_mock.call_args_list,
                [
                    call(["virsh", "net-define", str(fake_xml_path)], sudo=True),
                    call(["virsh", "net-autostart", "demo-net"], sudo=True),
                    call(["virsh", "net-start", "demo-net"], sudo=True),
                ],
            )

    def test_create_nat_network_returns_when_network_already_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_path(path_str):
                if path_str == "/tmp":
                    return Path(tmpdir)
                return Path(path_str)

            with patch.object(provision, "Path", side_effect=fake_path), patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0})(),
            ), patch.object(provision, "run") as run_mock:
                provision.create_nat_network(
                    "demo",
                    {
                        "name": "demo-net",
                        "gateway": "192.168.240.1",
                        "mac": "52:54:00:aa:bb:cc",
                        "vm_ip": "192.168.240.50",
                        "dhcp_start": "192.168.240.50",
                        "dhcp_end": "192.168.240.99",
                    },
                )

        run_mock.assert_not_called()

    def test_create_nat_network_cleans_stale_bridge_before_define(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_xml_path = Path(tmpdir) / "demo-net.xml"

            def fake_path(path_str):
                if path_str == "/tmp":
                    return Path(tmpdir)
                return Path(path_str)

            with patch.object(provision, "Path", side_effect=fake_path), patch.object(
                provision, "default_nat_bridge_name", return_value="virbr-demo"
            ), patch.object(
                provision.subprocess,
                "run",
                side_effect=[
                    type("Result", (), {"returncode": 1})(),
                    type("Result", (), {"returncode": 0})(),
                ],
            ), patch.object(provision, "run") as run_mock:
                provision.create_nat_network(
                    "demo",
                    {
                        "name": "demo-net",
                        "gateway": "192.168.240.1",
                        "mac": "52:54:00:aa:bb:cc",
                        "vm_ip": "192.168.240.50",
                        "dhcp_start": "192.168.240.50",
                        "dhcp_end": "192.168.240.99",
                    },
                )

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    ["ip", "link", "delete", "virbr-demo", "type", "bridge"],
                    sudo=True,
                    check=False,
                ),
                call(["virsh", "net-define", str(fake_xml_path)], sudo=True),
                call(["virsh", "net-autostart", "demo-net"], sudo=True),
                call(["virsh", "net-start", "demo-net"], sudo=True),
            ],
        )

    def test_vm_exists_reflects_subprocess_status(self):
        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 0})(),
        ):
            self.assertTrue(provision.vm_exists("demo"))

        with patch.object(
            provision.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 1})(),
        ):
            self.assertFalse(provision.vm_exists("demo"))

    def test_virt_install_skips_existing_vm(self):
        with patch.object(provision, "vm_exists", return_value=True), patch.object(
            provision, "run"
        ) as run_mock:
            provision.virt_install(
                "demo",
                {},
                "network=demo",
                Path("/vm.qcow2"),
                Path("/seed.iso"),
                "ubuntu24.04",
            )

        run_mock.assert_not_called()

    def test_virt_install_runs_command_for_new_vm(self):
        vm = {"ram_mb": 4096, "vcpus": 2}
        with patch.object(provision, "vm_exists", return_value=False), patch.object(
            provision, "run"
        ) as run_mock:
            provision.virt_install(
                "demo",
                vm,
                "network=demo-net,model=virtio,mac=52:54:00:aa:bb:cc",
                Path("/images/demo.qcow2"),
                Path("/images/demo-seed.iso"),
                "ubuntu24.04",
            )

        run_mock.assert_called_once_with(
            [
                "virt-install",
                "--name",
                "demo",
                "--memory",
                "4096",
                "--vcpus",
                "2",
                "--disk",
                f"path={Path('/images/demo.qcow2')},format=qcow2,bus=virtio",
                "--disk",
                f"path={Path('/images/demo-seed.iso')},device=cdrom",
                "--os-variant",
                "ubuntu24.04",
                "--network",
                "network=demo-net,model=virtio,mac=52:54:00:aa:bb:cc",
                "--graphics",
                "none",
                "--import",
                "--noautoconsole",
            ],
            sudo=True,
        )


class CleanupTests(unittest.TestCase):
    def test_cleanup_local_vm_artifacts_removes_keys_vm_data_and_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            vm_data_dir = tmpdir_path / "vm-data" / "demo"
            vm_data_dir.mkdir(parents=True)
            key_path = vm_data_dir / "demo_admin_ed25519"
            key_path.write_text("private", encoding="utf-8")
            pub_path = Path(str(key_path) + ".pub")
            pub_path.write_text("public", encoding="utf-8")

            with patch.object(provision, "delete_vm_state") as delete_state_mock:
                provision.cleanup_local_vm_artifacts(
                    "demo",
                    admin_private_key=key_path,
                    vm_data_dir=vm_data_dir,
                )

        self.assertFalse(key_path.exists())
        self.assertFalse(pub_path.exists())
        self.assertFalse(vm_data_dir.exists())
        delete_state_mock.assert_called_once_with("demo")

    def test_cleanup_local_vm_artifacts_uses_default_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_key_dir = Path(tmpdir) / "vm" / "keys" / "admin"
            admin_key_dir.mkdir(parents=True)
            key_path = admin_key_dir / "demo_admin_ed25519"
            key_path.write_text("private", encoding="utf-8")
            Path(str(key_path) + ".pub").write_text("public", encoding="utf-8")

            vm_data_dir = Path(tmpdir) / "vm" / "data" / "demo"
            vm_data_dir.mkdir(parents=True)
            (vm_data_dir / "user-data").write_text("user", encoding="utf-8")

            with patch.object(
                provision, "default_vm_data_dir", return_value=vm_data_dir
            ), patch.object(
                provision, "admin_private_key_path", return_value=key_path
            ), patch.object(provision, "delete_vm_state") as delete_state_mock:
                provision.cleanup_local_vm_artifacts("demo")

        self.assertFalse(key_path.exists())
        self.assertFalse(Path(str(key_path) + ".pub").exists())
        self.assertFalse(vm_data_dir.exists())
        delete_state_mock.assert_called_once_with("demo")

    def test_cleanup_local_vm_artifacts_tolerates_missing_files(self):
        with patch.object(
            provision, "default_vm_data_dir", return_value=Path("/missing/vm-data")
        ), patch.object(
            provision, "admin_private_key_path", return_value=Path("/missing/demo_admin_ed25519")
        ), patch.object(provision, "delete_vm_state") as delete_state_mock:
            provision.cleanup_local_vm_artifacts("demo")

        delete_state_mock.assert_called_once_with("demo")

    def test_cleanup_vm_storage_removes_both_images(self):
        with patch.object(provision, "run") as run_mock:
            provision.cleanup_vm_storage("demo")

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(["rm", "-f", str(provision.IMG_DIR / "demo.qcow2")], sudo=True, check=False),
                call(
                    ["rm", "-f", str(provision.IMG_DIR / "demo-seed.iso")],
                    sudo=True,
                    check=False,
                ),
            ],
        )
