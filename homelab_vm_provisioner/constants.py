"""Project-wide constants used by the provisioner."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"

PROVIDER_USER = "vmadmin"
PROVIDER_KEY_DIR = PROJECT_DIR / "provider-keys"
BUILD_DIR = PROJECT_DIR / ".build"

IMG_DIR = Path("/var/lib/libvirt/images")
BASE_IMG_NAME = "debian-12-generic-amd64.qcow2"
BASE_IMG_URL = "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"
OS_VARIANT = "debian12"

DEFAULT_REQUIRED_TOOLS = (
    "virsh",
    "virt-install",
    "qemu-img",
    "cloud-localds",
    "firewall-cmd",
    "ssh-keygen",
    "wget",
)

INSTALL_HINT = (
    "sudo apt install -y libvirt-daemon-system virtinst qemu-utils "
    "cloud-image-utils firewalld wget openssh-client python3-yaml python3-jinja2"
)

BLOCKED_PRIVATE_RANGES = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "169.254.0.0/16",
]
