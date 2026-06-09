"""Networking helpers for VM addressing and libvirt network discovery."""

import ipaddress
import random
import subprocess
import xml.etree.ElementTree as ET

from .system import capture_or_none


def random_mac():
    """Generate a libvirt-friendly random MAC address.

    Returns:
        str: Random MAC address in the ``52:54:00:xx:xx:xx`` range.
    """
    return "52:54:00:%02x:%02x:%02x" % (
        random.randint(0, 255),
        random.randint(0, 255),
        random.randint(0, 255),
    )


def get_existing_routes_text():
    """Return the host routing table as plain text.

    Returns:
        str: Output from ``ip route``.
    """
    result = subprocess.run(
        ["ip", "route"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.stdout


def get_existing_virsh_networks_text():
    """Return XML for all known libvirt networks.

    Returns:
        str: Concatenated output from ``virsh net-dumpxml``.
    """
    result = subprocess.run(
        ["sudo", "virsh", "net-list", "--all", "--name"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    xml = ""
    for net_name in result.stdout.splitlines():
        net_name = net_name.strip()
        if not net_name:
            continue

        xml_result = subprocess.run(
            ["sudo", "virsh", "net-dumpxml", net_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        xml += xml_result.stdout + "\n"

    return xml


def list_virsh_network_names():
    """List all libvirt network names.

    Returns:
        list[str]: Network names visible to ``virsh``.
    """
    result = subprocess.run(
        ["sudo", "virsh", "net-list", "--all", "--name"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def subnet_appears_used(prefix):
    """Return whether a subnet prefix appears in host or libvirt state.

    Args:
        prefix: Prefix string such as ``192.168.240.``.

    Returns:
        bool: ``True`` when the prefix is already present.
    """
    haystack = get_existing_routes_text() + "\n" + get_existing_virsh_networks_text()
    return prefix in haystack


def pick_free_subnet():
    """Pick an unused ``192.168.X.0/24`` subnet for NAT networking.

    Returns:
        dict: Generated network settings for the selected subnet.

    Raises:
        RuntimeError: If no free subnet can be found.
    """
    for third_octet in range(100, 251):
        prefix = f"192.168.{third_octet}"
        if subnet_appears_used(prefix + "."):
            continue

        return {
            "prefix": prefix,
            "cidr": f"{prefix}.0/24",
            "gateway": f"{prefix}.1",
            "vm_ip": f"{prefix}.50",
            "dhcp_start": f"{prefix}.50",
            "dhcp_end": f"{prefix}.99",
        }

    raise RuntimeError("Could not find free 192.168.X.0/24 subnet")


def parse_network_from_xml(xml_text, vm_name):
    """Parse libvirt network XML for the DHCP reservation of one VM.

    Args:
        xml_text: XML returned by ``virsh net-dumpxml``.
        vm_name: VM name to locate inside the DHCP host entries.

    Returns:
        dict | None: NAT network details for the VM, or ``None`` when the XML
        does not describe the VM.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    host = root.find(f".//dhcp/host[@name='{vm_name}']")
    if host is None:
        return None

    name = root.findtext("name")
    ip_node = root.find("ip")
    if ip_node is None:
        return None

    gateway = ip_node.get("address")
    netmask = ip_node.get("netmask")
    vm_ip = host.get("ip")
    mac = host.get("mac")
    if not (name and gateway and netmask and vm_ip):
        return None

    cidr = str(ipaddress.ip_network(f"{gateway}/{netmask}", strict=False))
    return {
        "mode": "nat",
        "name": name,
        "gateway": gateway,
        "cidr": cidr,
        "vm_ip": vm_ip,
        "mac": mac,
    }


def discover_vm_network(vm_name):
    """Discover the libvirt NAT network associated with a VM.

    Args:
        vm_name: VM name.

    Returns:
        dict | None: Matching network details, or ``None`` when no matching
        libvirt DHCP reservation is found.
    """
    for net_name in list_virsh_network_names():
        xml_text = capture_or_none(["virsh", "net-dumpxml", net_name], sudo=True)
        if not xml_text:
            continue

        network = parse_network_from_xml(xml_text, vm_name)
        if network:
            return network

    return None


def parse_ipv4_from_domifaddr(text):
    """Extract the first IPv4 address from ``virsh domifaddr`` output.

    Args:
        text: Text output from ``virsh domifaddr``.

    Returns:
        str | None: IPv4 address without the CIDR suffix, or ``None`` when no
        IPv4 address is present.
    """
    for line in text.splitlines():
        if "ipv4" not in line.lower():
            continue

        fields = line.split()
        if len(fields) < 4:
            continue

        try:
            address = ipaddress.ip_interface(fields[3]).ip
        except ValueError:
            continue

        if address.version == 4:
            return str(address)

    return None


def resolve_vm_ipv4(vm_name):
    """Resolve a VM IPv4 address using libvirt address sources.

    Args:
        vm_name: VM name.

    Returns:
        tuple[str | None, str | None]: The resolved IPv4 address and the libvirt
        source used, or ``(None, None)`` when resolution fails.
    """
    for source in ("lease", "agent", "arp"):
        result = subprocess.run(
            ["sudo", "virsh", "domifaddr", vm_name, "--source", source],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode != 0:
            continue

        address = parse_ipv4_from_domifaddr(result.stdout)
        if address:
            return address, source

    return None, None
