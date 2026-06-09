"""Firewalld helpers for VM NAT policy management."""

from .constants import BLOCKED_PRIVATE_RANGES
from .system import capture, capture_or_none, run, tool_exists


def forward_port_spec(port, vm_ip):
    """Build a firewalld forward-port specification string.

    Args:
        port: Port mapping dictionary.
        vm_ip: Destination VM IP address.

    Returns:
        str: Forward-port specification accepted by ``firewall-cmd``.
    """
    return (
        f"port={port['host']}:proto={port.get('proto', 'tcp')}:"
        f"toaddr={vm_ip}:toport={port['guest']}"
    )


def apply_firewalld_nat_policy(network, trust, ports):
    """Apply host firewalld policy for a NAT-backed VM.

    Args:
        network: NAT network settings.
        trust: Trust level from the VM config.
        ports: Host-to-guest port forwarding rules.

    Returns:
        bool: ``True`` when the VM-specific zone had to be created.
    """
    zone = network["zone"]
    cidr = network["cidr"]
    vm_ip = network["vm_ip"]

    existing_zones = capture(["firewall-cmd", "--permanent", "--get-zones"], sudo=True)
    zone_created = zone not in existing_zones.split()
    if zone_created:
        run(["firewall-cmd", "--permanent", "--new-zone", zone], sudo=True)

    run(["firewall-cmd", "--permanent", "--zone", zone, "--add-source", cidr], sudo=True)

    if trust == "untrusted":
        for blocked_range in BLOCKED_PRIVATE_RANGES:
            run(
                [
                    "firewall-cmd",
                    "--permanent",
                    "--zone",
                    zone,
                    "--add-rich-rule",
                    f'rule family="ipv4" destination address="{blocked_range}" reject',
                ],
                sudo=True,
                check=False,
            )

    for port in ports:
        run(
            [
                "firewall-cmd",
                "--permanent",
                f"--add-forward-port={forward_port_spec(port, vm_ip)}",
            ],
            sudo=True,
        )

    run(["firewall-cmd", "--reload"], sudo=True)
    return zone_created


def firewalld_zone_exists(zone):
    """Return whether a firewalld zone exists.

    Args:
        zone: Zone name.

    Returns:
        bool: ``True`` when the zone exists.
    """
    zones = capture_or_none(["firewall-cmd", "--permanent", "--get-zones"], sudo=True)
    return bool(zones and zone in zones.split())


def firewalld_zone_for_cidr(cidr, preferred_zone=None):
    """Find the zone that owns a CIDR source.

    Args:
        cidr: Source CIDR to locate.
        preferred_zone: Zone to check first when provided.

    Returns:
        str | None: Matching zone name, or ``None`` when no zone contains the
        source.
    """
    zones = capture_or_none(["firewall-cmd", "--permanent", "--get-zones"], sudo=True)
    if not zones:
        return None

    candidates = zones.split()
    if preferred_zone and preferred_zone in candidates:
        candidates = [preferred_zone] + [zone for zone in candidates if zone != preferred_zone]

    for zone in candidates:
        sources = capture_or_none(
            ["firewall-cmd", "--permanent", "--zone", zone, "--list-sources"],
            sudo=True,
        )
        if sources and cidr in sources.split():
            return zone

    return None


def list_zone_forward_ports(zone=None):
    """List forward-port rules for a zone.

    Args:
        zone: Optional zone name. When omitted, list top-level permanent rules.

    Returns:
        list[str]: Forward-port specifications.
    """
    cmd = ["firewall-cmd", "--permanent"]
    if zone is not None:
        cmd.extend(["--zone", zone])
    cmd.append("--list-forward-ports")

    output = capture_or_none(cmd, sudo=True)
    return output.split() if output else []


def find_forward_port_rules_for_vm(vm_ip):
    """Find forward-port rules that target a specific VM IP.

    Args:
        vm_ip: VM IP address.

    Returns:
        list[tuple[str | None, str]]: Zone/spec pairs for matching rules.
    """
    rules = []
    seen = set()

    zones = capture_or_none(["firewall-cmd", "--permanent", "--get-zones"], sudo=True)
    zone_names = zones.split() if zones else []

    for zone in [None] + zone_names:
        for spec in list_zone_forward_ports(zone):
            if f"toaddr={vm_ip}" not in spec:
                continue

            key = (zone, spec)
            if key in seen:
                continue

            seen.add(key)
            rules.append(key)

    return rules


def remove_forward_port_rule(spec, zone=None):
    """Remove a forward-port rule from firewalld.

    Args:
        spec: Forward-port specification.
        zone: Optional zone name.
    """
    cmd = ["firewall-cmd", "--permanent"]
    if zone is not None:
        cmd.extend(["--zone", zone])
    cmd.append(f"--remove-forward-port={spec}")
    run(cmd, sudo=True, check=False)


def firewalld_zone_is_empty(zone):
    """Return whether a firewalld zone has no remaining configured rules.

    Args:
        zone: Zone name.

    Returns:
        bool: ``True`` when the zone is empty.
    """
    checks = [
        "--list-sources",
        "--list-interfaces",
        "--list-services",
        "--list-ports",
        "--list-protocols",
        "--list-forward-ports",
        "--list-source-ports",
        "--list-icmp-blocks",
        "--list-rich-rules",
    ]

    for check in checks:
        value = capture_or_none(["firewall-cmd", "--permanent", "--zone", zone, check], sudo=True)
        if value is None or value.strip():
            return False

    return True


def cleanup_firewalld_vm_policy(vm_name, network, ports):
    """Remove VM-specific firewalld state.

    Args:
        vm_name: VM name.
        network: Stored or discovered network settings.
        ports: Stored port forwarding rules.
    """
    if not tool_exists("firewall-cmd"):
        return

    cleanup_attempted = False
    vm_ip = network.get("vm_ip")
    cidr = network.get("cidr")
    preferred_zone = network.get("zone") or f"{vm_name}-zone"

    zone = None
    if preferred_zone and firewalld_zone_exists(preferred_zone):
        zone = preferred_zone
    elif cidr:
        zone = firewalld_zone_for_cidr(cidr, preferred_zone=preferred_zone)

    rules = find_forward_port_rules_for_vm(vm_ip) if vm_ip else []
    for rule_zone, spec in rules:
        remove_forward_port_rule(spec, zone=rule_zone)
        cleanup_attempted = True

    if not rules and vm_ip:
        for port in ports:
            remove_forward_port_rule(forward_port_spec(port, vm_ip))
            cleanup_attempted = True

    if zone:
        if cidr:
            run(
                ["firewall-cmd", "--permanent", "--zone", zone, "--remove-source", cidr],
                sudo=True,
                check=False,
            )
            cleanup_attempted = True

        for blocked_range in BLOCKED_PRIVATE_RANGES:
            run(
                [
                    "firewall-cmd",
                    "--permanent",
                    "--zone",
                    zone,
                    "--remove-rich-rule",
                    f'rule family="ipv4" destination address="{blocked_range}" reject',
                ],
                sudo=True,
                check=False,
            )
            cleanup_attempted = True

        if firewalld_zone_is_empty(zone):
            run(["firewall-cmd", "--permanent", "--delete-zone", zone], sudo=True, check=False)
            cleanup_attempted = True

    if cleanup_attempted:
        run(["firewall-cmd", "--reload"], sudo=True, check=False)
