"""CLI orchestration for VM lifecycle commands."""

import argparse
import ipaddress
import subprocess
from pathlib import Path

from .config import (
    default_admin_key_dir,
    image_settings_for_config,
    load_config,
    load_global_config,
    load_vm_state,
    resolve_config_path,
    resolve_user_key_path,
    save_vm_state,
    vm_data_dir_for_config,
)
from .constants import ADMIN_USER
from .firewall import apply_firewalld_nat_policy, cleanup_firewalld_vm_policy
from .network import discover_vm_network, pick_free_subnet, random_mac, resolve_vm_ipv4
from .provision import (
    admin_keypair,
    admin_private_key_path,
    bridge_interface_exists,
    cleanup_bridge_interface,
    cleanup_local_vm_artifacts,
    cleanup_vm_storage,
    create_nat_network,
    create_seed_iso,
    create_vm_disk,
    default_nat_bridge_name,
    ensure_base_image,
    legacy_nat_bridge_name,
    render_templates,
    validate_os_variant,
    virt_install,
    vm_exists,
)
from .system import require_tools, run

MAX_VM_NAME_LENGTH = 12


def validate_vm_name(vm_name):
    """Validate the VM name against derived firewalld resource limits.

    Args:
        vm_name: VM name from config.

    Raises:
        ValueError: If the VM name is too long for the default derived zone name.
    """
    if len(vm_name) <= MAX_VM_NAME_LENGTH:
        return

    raise ValueError(
        "vm.name must be 12 characters or fewer so the default firewalld zone name "
        f"'{vm_name}-zone' stays within the 17-character limit"
    )


def _validate_nat_custom_network(network):
    """Validate explicit NAT custom network values.

    Args:
        network: Effective NAT custom network settings.

    Raises:
        ValueError: If the CIDR or any related IP address is invalid.
    """
    cidr_text = network["cidr"]
    try:
        cidr = ipaddress.ip_network(cidr_text, strict=True)
    except ValueError as exc:
        raise ValueError(f"network.cidr must be a valid IPv4 /24 network: {cidr_text}") from exc

    if cidr.version != 4 or cidr.prefixlen != 24:
        raise ValueError(f"network.cidr must be a valid IPv4 /24 network: {cidr_text}")

    for field in ("gateway", "vm_ip", "dhcp_start", "dhcp_end"):
        value = network[field]
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError(f"network.{field} must be a valid IPv4 address: {value}") from exc

        if address.version != 4:
            raise ValueError(f"network.{field} must be a valid IPv4 address: {value}")
        if address not in cidr:
            raise ValueError(f"network.{field} must be inside network.cidr {cidr_text}: {value}")

    dhcp_start = ipaddress.ip_address(network["dhcp_start"])
    dhcp_end = ipaddress.ip_address(network["dhcp_end"])
    if dhcp_start > dhcp_end:
        raise ValueError(
            "network.dhcp_start must not be greater than network.dhcp_end: "
            f"{dhcp_start} > {dhcp_end}"
        )


def build_network_config(vm_name, net_cfg):
    """Build the effective network settings for a VM.

    Args:
        vm_name: VM name.
        net_cfg: Raw ``network`` config section.

    Returns:
        dict: Effective network settings.

    Raises:
        ValueError: If ``network.mode`` is invalid or incomplete.
    """
    mode = net_cfg.get("mode", "nat-auto")
    network = {
        "mode": mode,
        "mac": net_cfg.get("mac", random_mac()),
    }

    if mode == "nat-auto":
        network.update(pick_free_subnet())
        network["name"] = net_cfg.get("name", f"{vm_name}-net")
        network["zone"] = net_cfg.get("zone", f"{vm_name}-zone")
        return network

    if mode == "nat-custom":
        prefix = net_cfg.get("subnet_prefix")
        if prefix:
            try:
                ipaddress.ip_network(f"{prefix}.0/24", strict=True)
            except ValueError as exc:
                raise ValueError(
                    f"network.subnet_prefix must be a valid IPv4 prefix like 192.168.240: {prefix}"
                ) from exc

            network["prefix"] = prefix
            network["cidr"] = net_cfg.get("cidr", f"{prefix}.0/24")
            network["gateway"] = net_cfg.get("gateway", f"{prefix}.1")
            network["vm_ip"] = net_cfg.get("vm_ip", f"{prefix}.50")
            network["dhcp_start"] = net_cfg.get("dhcp_start", f"{prefix}.50")
            network["dhcp_end"] = net_cfg.get("dhcp_end", f"{prefix}.99")
        else:
            required = ["cidr", "gateway", "vm_ip", "dhcp_start", "dhcp_end"]
            missing = [field for field in required if field not in net_cfg]
            if missing:
                raise ValueError(f"Missing nat-custom network fields: {missing}")

            for field in required:
                network[field] = net_cfg[field]

        _validate_nat_custom_network(network)
        network["name"] = net_cfg.get("name", f"{vm_name}-net")
        network["zone"] = net_cfg.get("zone", f"{vm_name}-zone")
        return network

    if mode == "bridge":
        network["bridge_name"] = net_cfg.get("bridge_name", "br0")
        network["vm_ip"] = net_cfg.get("vm_ip", "dhcp-from-router")
        network["cidr"] = net_cfg.get("cidr", "main-lan")
        return network

    raise ValueError("network.mode must be nat-auto, nat-custom, or bridge")


def build_render_context(
    vm_name,
    admin_public_key,
    vm_user,
    vm_public_key,
    allow_sudo,
    packages,
):
    """Build the cloud-init template context for a VM.

    Args:
        vm_name: VM name.
        admin_public_key: Admin public SSH key.
        vm_user: Tenant username.
        vm_public_key: Tenant public SSH key, or ``None`` when it will be added
            later.
        allow_sudo: Whether the tenant gets passwordless sudo.
        packages: Extra packages to install.

    Returns:
        dict: Template context for cloud-init rendering.
    """
    return {
        "vm_name": vm_name,
        "admin_user": ADMIN_USER,
        "admin_public_key": admin_public_key,
        "vm_user": vm_user,
        "vm_public_key": vm_public_key,
        "vm_sudo": "ALL=(ALL) NOPASSWD:ALL" if allow_sudo else "false",
        "packages": packages,
    }


def print_create_summary(vm_name, vm_user, trust, network, admin_private_key, ports):
    """Print the post-create connection summary for a VM.

    Args:
        vm_name: VM name.
        vm_user: Tenant username.
        trust: VM trust level.
        network: Effective network settings.
        admin_private_key: Admin private key path.
        ports: Port forwarding rules.
    """
    print()
    print("Created VM")
    print("==========")
    print(f"Name:          {vm_name}")
    print(f"Tenant user:   {vm_user}")
    print(f"Admin user:    {ADMIN_USER}")
    print(f"Trust:         {trust}")
    print(f"Network mode:  {network['mode']}")
    print(f"VM IP:         {network.get('vm_ip')}")
    print(f"MAC:           {network.get('mac')}")
    print()
    print("Admin key:")
    print(f"  {admin_private_key}")
    print()
    print("Admin SSH helper:")
    print(f"  ./vmssh-admin {vm_name}")
    print()

    ssh_port = None
    for port in ports:
        if int(port["guest"]) == 22:
            ssh_port = port["host"]

    if network["mode"].startswith("nat") and ssh_port:
        print("Admin SSH:")
        print(f"  ssh -i {admin_private_key} {ADMIN_USER}@HOST_IP -p {ssh_port}")
        print()
        print("Tenant SSH:")
        print(f"  ssh {vm_user}@HOST_IP -p {ssh_port}")
    elif network["mode"] == "bridge":
        print("Admin SSH:")
        print(f"  ssh -i {admin_private_key} {ADMIN_USER}@VM_LAN_IP")
        print()
        print("Tenant SSH:")
        print(f"  ssh {vm_user}@VM_LAN_IP")


def create(config_path):
    """Create a VM from a YAML config.

    Args:
        config_path: Config path or shorthand.

    Raises:
        FileNotFoundError: If the config or tenant SSH key is missing.
        ValueError: If the config contains invalid values.
    """
    require_tools()

    resolved_config_path = resolve_config_path(config_path)
    global_config = load_global_config()
    config_data = load_config(resolved_config_path)
    vm = config_data["vm"]
    net_cfg = config_data.get("network", {})
    packages = config_data.get("packages", [])
    ports = config_data.get("ports", [])

    vm_name = vm["name"]
    vm_user = vm["user"]
    vm_ssh_key_file = None
    if vm.get("ssh_key_file"):
        vm_ssh_key_file = resolve_user_key_path(vm["ssh_key_file"], global_config=global_config)
    allow_sudo = bool(vm.get("allow_sudo", False))
    trust = vm.get("trust", "untrusted")
    template = vm.get("template", "base")

    if trust not in ("trusted", "untrusted"):
        raise ValueError("vm.trust must be trusted or untrusted")
    validate_vm_name(vm_name)
    if vm_ssh_key_file is not None and not vm_ssh_key_file.exists():
        raise FileNotFoundError(f"Missing VM SSH key file: {vm_ssh_key_file}")

    vm_data_dir = vm_data_dir_for_config(vm_name, config_data, global_config=global_config)
    image_settings = image_settings_for_config(config_data, global_config=global_config)
    validate_os_variant(image_settings["os_variant"])
    admin_private_key, admin_public_key = admin_keypair(
        vm_name,
        admin_key_dir=default_admin_key_dir(global_config),
    )
    vm_public_key = None
    if vm_ssh_key_file is not None:
        vm_public_key = vm_ssh_key_file.read_text(encoding="utf-8").strip()
    network = build_network_config(vm_name, net_cfg)
    context = build_render_context(
        vm_name,
        admin_public_key,
        vm_user,
        vm_public_key,
        allow_sudo,
        packages,
    )

    state = {
        "vm_name": vm_name,
        "config_path": str(resolved_config_path),
        "trust": trust,
        "vm_data_dir": str(vm_data_dir),
        "network": network,
        "ports": ports,
        "admin_private_key": str(admin_private_key),
    }
    save_vm_state(vm_name, state)

    run(["systemctl", "enable", "--now", "libvirtd"], sudo=True)
    run(["systemctl", "enable", "--now", "firewalld"], sudo=True)

    base_img = ensure_base_image(image_settings)
    vm_disk = create_vm_disk(vm_name, vm["disk_gb"], base_img)
    if network["mode"].startswith("nat"):
        create_nat_network(vm_name, network)
        network_arg = f'network={network["name"]},model=virtio,mac={network["mac"]}'
    else:
        network_arg = f'bridge={network["bridge_name"]},model=virtio,mac={network["mac"]}'

    user_data, meta_data = render_templates(context, template, vm_data_dir)
    seed_iso = create_seed_iso(vm_name, user_data, meta_data)
    virt_install(vm_name, vm, network_arg, vm_disk, seed_iso, image_settings["os_variant"])

    if network["mode"].startswith("nat"):
        state["firewalld"] = {
            "zone_created": apply_firewalld_nat_policy(network, trust, ports),
        }
        save_vm_state(vm_name, state)
    else:
        print("Bridge mode selected: skipping host NAT firewall/port-forward rules.")
        print("Use your router/VLAN firewall for isolation.")

    print_create_summary(vm_name, vm_user, trust, network, admin_private_key, ports)


def ssh_admin(vm_name, vm_ip=None):
    """Open an SSH session to the per-VM admin account.

    Args:
        vm_name: VM name.
        vm_ip: Optional IP override.

    Raises:
        FileNotFoundError: If the admin key is missing.
        RuntimeError: If the VM does not exist or its IP cannot be resolved.
        SystemExit: With the exit code from the ``ssh`` process.
    """
    require_tools(["virsh", "ssh"])

    if not vm_exists(vm_name):
        raise RuntimeError(f"VM not found: {vm_name}")

    global_config = load_global_config()
    state = load_vm_state(vm_name)
    if state.get("admin_private_key"):
        admin_private_key = Path(state["admin_private_key"])
    else:
        admin_private_key = admin_private_key_path(
            vm_name,
            admin_key_dir=default_admin_key_dir(global_config),
        )

    if not admin_private_key.exists():
        raise FileNotFoundError(
            f"Missing admin SSH key for {vm_name}: {admin_private_key}"
        )

    source = None
    if vm_ip is None:
        vm_ip, source = resolve_vm_ipv4(vm_name)
    if vm_ip is None:
        raise RuntimeError(
            "Could not determine the VM IP automatically. Retry with --ip <address>."
        )

    if source is not None:
        print(f"Resolved {vm_name} to {vm_ip} via libvirt {source}.")
    else:
        print(f"Using provided IP for {vm_name}: {vm_ip}")

    cmd = [
        "ssh",
        "-i",
        str(admin_private_key),
        "-o",
        "IdentitiesOnly=yes",
        f"{ADMIN_USER}@{vm_ip}",
    ]
    print("+", " ".join(str(x) for x in cmd))
    result = subprocess.run(cmd)
    raise SystemExit(result.returncode)


def destroy(vm_name):
    """Destroy a VM and remove its associated host artifacts.

    Args:
        vm_name: VM name.
    """
    state = load_vm_state(vm_name)
    network = dict(state.get("network") or {})
    network.update(discover_vm_network(vm_name) or {})
    ports = state.get("ports") or []

    network.setdefault("name", f"{vm_name}-net")
    network.setdefault("zone", f"{vm_name}-zone")

    if vm_exists(vm_name):
        run(["virsh", "destroy", vm_name], sudo=True, check=False)
        run(["virsh", "undefine", vm_name, "--remove-all-storage"], sudo=True, check=False)

    cleanup_firewalld_vm_policy(vm_name, network, ports)
    run(["virsh", "net-destroy", network["name"]], sudo=True, check=False)
    run(["virsh", "net-undefine", network["name"]], sudo=True, check=False)

    for bridge_name in {
        network.get("bridge_name"),
        default_nat_bridge_name(vm_name),
        legacy_nat_bridge_name(vm_name),
    }:
        if bridge_name and bridge_interface_exists(bridge_name):
            cleanup_bridge_interface(bridge_name)

    cleanup_vm_storage(vm_name)
    cleanup_local_vm_artifacts(
        vm_name,
        admin_private_key=state.get("admin_private_key"),
        vm_data_dir=state.get("vm_data_dir"),
    )


def build_parser():
    """Build the top-level argument parser.

    Returns:
        argparse.ArgumentParser: Configured CLI parser.
    """
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)

    create_parser = subcommands.add_parser("create")
    create_parser.add_argument("config")

    destroy_parser = subcommands.add_parser("destroy")
    destroy_parser.add_argument("name")

    ssh_admin_parser = subcommands.add_parser("ssh-admin")
    ssh_admin_parser.add_argument("name")
    ssh_admin_parser.add_argument("--ip")
    return parser


def main(argv=None):
    """Run the CLI entrypoint.

    Args:
        argv: Optional argument vector for programmatic invocation.
    """
    args = build_parser().parse_args(argv)

    if args.command == "create":
        create(args.config)
    elif args.command == "destroy":
        destroy(args.name)
    elif args.command == "ssh-admin":
        ssh_admin(args.name, args.ip)
