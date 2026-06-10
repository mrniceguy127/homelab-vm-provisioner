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


def direct_forward_rule_args(port, vm_ip):
    """Build a firewalld direct FORWARD accept rule.

    Args:
        port: Port mapping dictionary.
        vm_ip: Destination VM IP address.

    Returns:
        list[str]: Rule arguments accepted by ``firewall-cmd --direct``.
    """
    return [
        "ipv4",
        "filter",
        "FORWARD",
        "-1000",
        "-p",
        port.get("proto", "tcp"),
        "-d",
        vm_ip,
        "--dport",
        str(port["guest"]),
        "-j",
        "ACCEPT",
    ]


def direct_forward_ports(ports):
    """Return the guest ports that need direct FORWARD accept rules.

    Args:
        ports: Configured host-to-guest port forwarding rules.

    Returns:
        list[dict]: Unique guest port/protocol mappings.
    """
    direct_ports = []
    seen = set()

    for port in ports:
        proto = port.get("proto", "tcp")
        guest = str(port["guest"])
        key = (proto, guest)
        if key in seen:
            continue

        seen.add(key)
        direct_ports.append({"guest": guest, "proto": proto})

    return direct_ports


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

    run(["firewall-cmd", "--permanent", "--zone", zone, "--set-target", "ACCEPT"], sudo=True)
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

    for port in direct_forward_ports(ports):
        run(
            [
                "firewall-cmd",
                "--permanent",
                "--direct",
                "--add-rule",
                *direct_forward_rule_args(port, vm_ip),
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


def list_direct_rules():
    """List permanent firewalld direct rules.

    Returns:
        list[list[str]]: Direct rules split into argument lists.
    """
    output = capture_or_none(
        ["firewall-cmd", "--permanent", "--direct", "--get-all-rules"],
        sudo=True,
    )
    if not output:
        return []

    return [line.split() for line in output.splitlines() if line.strip()]


def find_direct_forward_rules_for_vm(vm_ip):
    """Find direct FORWARD accept rules that target a VM IP.

    Args:
        vm_ip: VM IP address.

    Returns:
        list[list[str]]: Matching direct rule argument lists.
    """
    rules = []
    for rule in list_direct_rules():
        if len(rule) < 4:
            continue
        if rule[:4] != ["ipv4", "filter", "FORWARD", "-1000"]:
            continue
        if "-d" not in rule:
            continue
        dest_index = rule.index("-d") + 1
        if dest_index >= len(rule) or rule[dest_index] != vm_ip:
            continue
        rules.append(rule)

    return rules


def remove_direct_rule(rule_args):
    """Remove a permanent firewalld direct rule.

    Args:
        rule_args: Direct rule argument list.
    """
    run(
        ["firewall-cmd", "--permanent", "--direct", "--remove-rule", *rule_args],
        sudo=True,
        check=False,
    )


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

    direct_rules = find_direct_forward_rules_for_vm(vm_ip) if vm_ip else []
    for rule in direct_rules:
        remove_direct_rule(rule)
        cleanup_attempted = True

    if not direct_rules and vm_ip:
        for port in direct_forward_ports(ports):
            remove_direct_rule(direct_forward_rule_args(port, vm_ip))
            cleanup_attempted = True

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
