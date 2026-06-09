"""Provisioning helpers for libvirt resources and local artifacts."""

import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .config import build_dir_for_vm
from .constants import (
    BASE_IMG_NAME,
    BASE_IMG_URL,
    IMG_DIR,
    OS_VARIANT,
    PROVIDER_KEY_DIR,
    TEMPLATES_DIR,
)
from .system import run


def provider_private_key_path(vm_name):
    """Return the admin private key path for a VM.

    Args:
        vm_name: VM name.

    Returns:
        Path: Private key path in ``provider-keys/``.
    """
    return PROVIDER_KEY_DIR / f"{vm_name}_provider_ed25519"


def provider_keypair(vm_name):
    """Ensure the per-VM admin SSH keypair exists.

    Args:
        vm_name: VM name.

    Returns:
        tuple[Path, str]: Private key path and public key contents.
    """
    PROVIDER_KEY_DIR.mkdir(mode=0o700, exist_ok=True)

    key_path = provider_private_key_path(vm_name)
    pub_path = Path(str(key_path) + ".pub")
    if not key_path.exists():
        run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-f",
                str(key_path),
                "-N",
                "",
                "-C",
                f"provider-{vm_name}",
            ]
        )

    key_path.chmod(0o600)
    pub_path.chmod(0o644)
    return key_path, pub_path.read_text(encoding="utf-8").strip()


def render_templates(context, template_name):
    """Render cloud-init templates for a VM.

    Args:
        context: Template variables for the VM.
        template_name: Base template name without the ``-user-data`` suffix.

    Returns:
        tuple[Path, Path]: Rendered ``user-data`` and ``meta-data`` file paths.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    user_data = env.get_template(f"{template_name}-user-data.yaml.j2").render(**context)
    meta_data = env.get_template("meta-data.yaml.j2").render(**context)

    build_dir = build_dir_for_vm(context["vm_name"])
    build_dir.mkdir(parents=True, exist_ok=True)

    user_data_path = build_dir / "user-data"
    meta_data_path = build_dir / "meta-data"
    user_data_path.write_text(user_data, encoding="utf-8")
    meta_data_path.write_text(meta_data, encoding="utf-8")
    return user_data_path, meta_data_path


def ensure_base_image():
    """Ensure the base cloud image exists locally.

    Returns:
        Path: Base image path.
    """
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    base_img = IMG_DIR / BASE_IMG_NAME
    if not base_img.exists():
        run(["wget", "-O", str(base_img), BASE_IMG_URL], sudo=True)
    return base_img


def create_vm_disk(vm_name, disk_gb, base_img):
    """Create a qcow2 VM disk backed by the base image.

    Args:
        vm_name: VM name.
        disk_gb: Disk size in gibibytes.
        base_img: Base image path.

    Returns:
        Path: Created or existing VM disk path.
    """
    vm_disk = IMG_DIR / f"{vm_name}.qcow2"
    if not vm_disk.exists():
        run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(base_img),
                str(vm_disk),
                f"{disk_gb}G",
            ],
            sudo=True,
        )
    return vm_disk


def create_seed_iso(vm_name, user_data_path, meta_data_path):
    """Build the cloud-init seed ISO for a VM.

    Args:
        vm_name: VM name.
        user_data_path: Rendered cloud-init ``user-data`` path.
        meta_data_path: Rendered cloud-init ``meta-data`` path.

    Returns:
        Path: Seed ISO path.
    """
    seed_iso = IMG_DIR / f"{vm_name}-seed.iso"
    run(
        [
            "cloud-localds",
            str(seed_iso),
            str(user_data_path),
            str(meta_data_path),
        ],
        sudo=True,
    )
    return seed_iso


def create_nat_network(vm_name, network):
    """Create and start a libvirt NAT network when needed.

    Args:
        vm_name: VM name.
        network: NAT network settings.
    """
    bridge_name = network.get("bridge_name", f"virbr-{vm_name[:6]}")
    xml = f"""
<network>
  <name>{network['name']}</name>
  <forward mode='nat'/>
  <bridge name='{bridge_name}' stp='on' delay='0'/>
  <ip address='{network['gateway']}' netmask='255.255.255.0'>
    <dhcp>
      <host mac='{network['mac']}' name='{vm_name}' ip='{network['vm_ip']}'/>
      <range start='{network['dhcp_start']}' end='{network['dhcp_end']}'/>
    </dhcp>
  </ip>
</network>
""".strip()

    xml_path = Path("/tmp") / f"{network['name']}.xml"
    xml_path.write_text(xml, encoding="utf-8")

    result = subprocess.run(
        ["sudo", "virsh", "net-info", network["name"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return

    run(["virsh", "net-define", str(xml_path)], sudo=True)
    run(["virsh", "net-autostart", network["name"]], sudo=True)
    run(["virsh", "net-start", network["name"]], sudo=True)


def vm_exists(vm_name):
    """Return whether a libvirt domain already exists.

    Args:
        vm_name: VM name.

    Returns:
        bool: ``True`` when the domain exists.
    """
    result = subprocess.run(
        ["sudo", "virsh", "dominfo", vm_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def virt_install(vm_name, vm, network_arg, vm_disk, seed_iso):
    """Create a VM with ``virt-install``.

    Args:
        vm_name: VM name.
        vm: VM settings from the user config.
        network_arg: Rendered ``virt-install --network`` argument.
        vm_disk: VM disk path.
        seed_iso: Seed ISO path.
    """
    if vm_exists(vm_name):
        print(f"VM already exists: {vm_name}")
        return

    run(
        [
            "virt-install",
            "--name",
            vm_name,
            "--memory",
            str(vm["ram_mb"]),
            "--vcpus",
            str(vm["vcpus"]),
            "--disk",
            f"path={vm_disk},format=qcow2,bus=virtio",
            "--disk",
            f"path={seed_iso},device=cdrom",
            "--os-variant",
            OS_VARIANT,
            "--network",
            network_arg,
            "--graphics",
            "none",
            "--import",
            "--noautoconsole",
        ],
        sudo=True,
    )


def cleanup_local_vm_artifacts(vm_name, provider_private_key=None):
    """Remove generated local files for a VM.

    Args:
        vm_name: VM name.
        provider_private_key: Optional admin private key path override.
    """
    if provider_private_key is None:
        provider_private_key = provider_private_key_path(vm_name)

    key_path = Path(provider_private_key)
    pub_path = Path(str(key_path) + ".pub")
    for path in (key_path, pub_path):
        if path.exists():
            path.unlink()

    build_dir = build_dir_for_vm(vm_name)
    if build_dir.exists():
        shutil.rmtree(build_dir)


def cleanup_vm_storage(vm_name):
    """Remove VM disk images left in the libvirt image directory.

    Args:
        vm_name: VM name.
    """
    for path in (IMG_DIR / f"{vm_name}.qcow2", IMG_DIR / f"{vm_name}-seed.iso"):
        run(["rm", "-f", str(path)], sudo=True, check=False)
