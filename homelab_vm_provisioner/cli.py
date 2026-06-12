"""CLI orchestration for VM lifecycle commands."""

import argparse
import ipaddress
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import (
    default_snapshot_root,
    default_admin_key_dir,
    dns_settings_for_config,
    image_settings_for_config,
    load_config,
    load_global_config,
    load_vm_state,
    resolve_setup_script_path,
    resolve_config_path,
    resolve_user_key_path,
    save_vm_state,
    state_file_for_vm,
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
    copy_image_artifact,
    copy_qcow2_image,
    create_nat_network,
    create_seed_iso,
    create_vm_disk,
    default_nat_bridge_name,
    ensure_base_image,
    legacy_nat_bridge_name,
    prepare_cloned_guest_disk,
    render_templates,
    seed_iso_path,
    validate_os_variant,
    virt_install,
    vm_disk_path,
    vm_exists,
)
from .system import capture_or_none, host_lifecycle_lock, require_tools, run

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
    dns_resolvers,
    setup_script_content,
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
        dns_resolvers: Default DNS servers configured inside the guest.
        setup_script_content: Optional guest setup script contents.

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
        "dns_resolvers": dns_resolvers,
        "setup_script_content": setup_script_content,
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


def load_setup_script_content(config_data, global_config):
    """Load the optional guest setup script contents from the VM config."""
    scripts_config = config_data.get("scripts") or {}
    setup_script_file = scripts_config.get("setup_script_file")
    if not setup_script_file:
        return None

    resolved_path = resolve_setup_script_path(setup_script_file, global_config=global_config)
    if not resolved_path.exists():
        raise FileNotFoundError(f"Missing setup script file: {resolved_path}")

    script_text = resolved_path.read_text(encoding="utf-8")
    if not script_text.endswith("\n"):
        script_text += "\n"
    return script_text


def build_network_arg(vm_name, network):
    """Build the ``virt-install --network`` argument for a VM."""
    if network["mode"].startswith("nat"):
        return f'network={network["name"]},model=virtio,mac={network["mac"]}'

    return f'bridge={network["bridge_name"]},model=virtio,mac={network["mac"]}'


def ensure_host_services():
    """Ensure the required libvirt and firewall services are enabled."""
    run(["systemctl", "enable", "--now", "libvirtd"], sudo=True)
    run(["systemctl", "enable", "--now", "firewalld"], sudo=True)


def build_vm_state(vm_name, resolved_config_path, trust, vm_data_dir, network, ports, admin_key_path):
    """Build the persisted state payload for a VM."""
    return {
        "vm_name": vm_name,
        "config_path": str(resolved_config_path),
        "trust": trust,
        "vm_data_dir": str(vm_data_dir),
        "network": network,
        "ports": ports,
        "admin_private_key": str(admin_key_path),
    }


def prepare_vm_definition(config_path):
    """Resolve a VM config into the effective provisioning inputs."""
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

    setup_script_content = load_setup_script_content(config_data, global_config)
    vm_data_dir = vm_data_dir_for_config(vm_name, config_data, global_config=global_config)
    image_settings = image_settings_for_config(config_data, global_config=global_config)
    dns_settings = dns_settings_for_config(config_data, global_config=global_config)
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
        dns_settings["resolvers"],
        setup_script_content,
    )

    return {
        "resolved_config_path": resolved_config_path,
        "global_config": global_config,
        "config_data": config_data,
        "vm": vm,
        "vm_name": vm_name,
        "vm_user": vm_user,
        "trust": trust,
        "template": template,
        "ports": ports,
        "network": network,
        "image_settings": image_settings,
        "vm_data_dir": vm_data_dir,
        "admin_private_key": admin_private_key,
        "render_context": context,
        "state": build_vm_state(
            vm_name,
            resolved_config_path,
            trust,
            vm_data_dir,
            network,
            ports,
            admin_private_key,
        ),
    }


def render_seed_iso_for_definition(definition):
    """Render cloud-init artifacts and build the VM seed ISO."""
    user_data, meta_data = render_templates(
        definition["render_context"],
        definition["template"],
        definition["vm_data_dir"],
    )
    return create_seed_iso(definition["vm_name"], user_data, meta_data)


def apply_runtime_networking(vm_name, network, trust, ports, state):
    """Apply NAT networks and firewall rules for the VM when required."""
    if network["mode"].startswith("nat"):
        state["firewalld"] = {
            "zone_created": apply_firewalld_nat_policy(network, trust, ports),
        }
        save_vm_state(vm_name, state)
        return

    print("Bridge mode selected: skipping host NAT firewall/port-forward rules.")
    print("Use your router/VLAN firewall for isolation.")


def merged_vm_network(vm_name, state):
    """Merge persisted VM network state with any live-discovered details."""
    network = dict(state.get("network") or {})
    network.update(discover_vm_network(vm_name) or {})
    network.setdefault("name", f"{vm_name}-net")
    network.setdefault("zone", f"{vm_name}-zone")
    return network


def current_domain_state(vm_name):
    """Return the current libvirt domain state string when available."""
    return capture_or_none(["virsh", "domstate", vm_name], sudo=True)


def stop_vm_domain(vm_name, timeout_seconds=60):
    """Stop a VM, falling back to forceful shutdown if needed."""
    if not vm_exists(vm_name):
        raise FileNotFoundError(f"VM not found: {vm_name}")

    state = (current_domain_state(vm_name) or "").strip().lower()
    if state and state != "running":
        print(f"VM already stopped: {vm_name}")
        return False

    run(["virsh", "shutdown", vm_name], sudo=True, check=False)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = (current_domain_state(vm_name) or "").strip().lower()
        if state and state != "running":
            return True
        time.sleep(2)

    run(["virsh", "destroy", vm_name], sudo=True, check=False)
    return True


def start_vm_domain(vm_name):
    """Start a VM when it is not already running."""
    if not vm_exists(vm_name):
        raise FileNotFoundError(f"VM not found: {vm_name}")

    state = (current_domain_state(vm_name) or "").strip().lower()
    if state == "running":
        print(f"VM already running: {vm_name}")
        return False

    run(["virsh", "start", vm_name], sudo=True)
    return True


def cleanup_vm_runtime_definition(vm_name, network, ports, remove_storage):
    """Remove the libvirt domain and network resources for a VM."""
    if vm_exists(vm_name):
        run(["virsh", "destroy", vm_name], sudo=True, check=False)
        undefine_cmd = ["virsh", "undefine", vm_name]
        if remove_storage:
            undefine_cmd.append("--remove-all-storage")
        run(undefine_cmd, sudo=True, check=False)

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

    if remove_storage:
        cleanup_vm_storage(vm_name)


def snapshot_root_for_vm(vm_name, global_config=None):
    """Return the restore point directory for a VM."""
    return default_snapshot_root(global_config) / vm_name


def snapshot_path_for_vm(vm_name, snapshot_id, global_config=None):
    """Return the restore point directory for one snapshot."""
    return snapshot_root_for_vm(vm_name, global_config) / snapshot_id


def snapshot_metadata_path(snapshot_path):
    """Return the metadata path inside a restore point directory."""
    return snapshot_path / "metadata.yaml"


def load_snapshot_metadata(vm_name, snapshot_id, global_config=None):
    """Load one restore point metadata payload."""
    metadata_path = snapshot_metadata_path(snapshot_path_for_vm(vm_name, snapshot_id, global_config))
    if not metadata_path.exists():
        raise FileNotFoundError(f"Snapshot not found for {vm_name}: {snapshot_id}")

    return yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}


def list_snapshots(vm_name):
    """List available restore points for a VM."""
    root = snapshot_root_for_vm(vm_name)
    if not root.exists():
        return []

    snapshots = []
    for metadata_file in sorted(root.glob("*/metadata.yaml")):
        metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8")) or {}
        snapshot_id = metadata.get("snapshot_id") or metadata_file.parent.name
        snapshots.append(
            {
                "snapshot_id": snapshot_id,
                "created_at": metadata.get("created_at"),
                "source_was_running": bool(metadata.get("source_was_running", False)),
                "path": str(metadata_file.parent),
            }
        )

    return sorted(snapshots, key=lambda item: item.get("created_at") or "", reverse=True)


def current_snapshot_id():
    """Return a timestamp-based restore point identifier."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def copy_local_file(source_path, target_path):
    """Copy a user-owned file into place."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)


def copy_local_tree(source_path, target_path):
    """Copy a user-owned directory tree into place."""
    if target_path.exists():
        shutil.rmtree(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_path, target_path)


def chown_path_to_current_user(target_path):
    """Restore ownership for snapshot files created via sudo."""
    uid_getter = getattr(os, "getuid", None)
    gid_getter = getattr(os, "getgid", None)
    if uid_getter is None or gid_getter is None:
        return

    run(["chown", "-R", f"{uid_getter()}:{gid_getter()}", str(target_path)], sudo=True)


def resolved_config_assets(config_data, global_config):
    """Resolve config-managed external files used by a VM definition."""
    vm = config_data.get("vm") or {}
    scripts = config_data.get("scripts") or {}
    assets = {}

    if vm.get("ssh_key_file"):
        assets["ssh_key_file"] = resolve_user_key_path(vm["ssh_key_file"], global_config=global_config)

    if scripts.get("setup_script_file"):
        assets["setup_script_file"] = resolve_setup_script_path(
            scripts["setup_script_file"],
            global_config=global_config,
        )

    return assets


def create(config_path):
    """Create a VM from a YAML config.

    Args:
        config_path: Config path or shorthand.

    Raises:
        FileNotFoundError: If the config or tenant SSH key is missing.
        ValueError: If the config contains invalid values.
    """
    require_tools()

    vm_name = load_config(resolve_config_path(config_path))["vm"]["name"]
    with host_lifecycle_lock("create", vm_name=vm_name):
        definition = prepare_vm_definition(config_path)
        vm_name = definition["vm_name"]
        save_vm_state(vm_name, definition["state"])

        ensure_host_services()
        base_img = ensure_base_image(definition["image_settings"])
        vm_disk = create_vm_disk(vm_name, definition["vm"]["disk_gb"], base_img)
        network_arg = build_network_arg(vm_name, definition["network"])
        seed_iso = render_seed_iso_for_definition(definition)

        if definition["network"]["mode"].startswith("nat"):
            create_nat_network(vm_name, definition["network"])

        virt_install(
            vm_name,
            definition["vm"],
            network_arg,
            vm_disk,
            seed_iso,
            definition["image_settings"]["os_variant"],
        )

        apply_runtime_networking(
            vm_name,
            definition["network"],
            definition["trust"],
            definition["ports"],
            definition["state"],
        )
        print_create_summary(
            vm_name,
            definition["vm_user"],
            definition["trust"],
            definition["network"],
            definition["admin_private_key"],
            definition["ports"],
        )


def start(vm_name):
    """Start an existing libvirt VM."""
    require_tools(["virsh"])

    with host_lifecycle_lock("start", vm_name=vm_name):
        started = start_vm_domain(vm_name)

    if started:
        print(f"Started VM: {vm_name}")


def stop(vm_name):
    """Stop an existing libvirt VM."""
    require_tools(["virsh"])

    with host_lifecycle_lock("stop", vm_name=vm_name):
        stopped = stop_vm_domain(vm_name)

    if stopped:
        print(f"Stopped VM: {vm_name}")


def snapshot_create(vm_name):
    """Create a restore point for a VM by copying its disk and host artifacts."""
    require_tools()

    state = load_vm_state(vm_name)
    config_path = state.get("config_path")
    if not config_path:
        raise FileNotFoundError(f"No saved config path was recorded for VM: {vm_name}")

    resolved_config_path = resolve_config_path(config_path)
    config_data = load_config(resolved_config_path)
    global_config = load_global_config()
    snapshot_id = current_snapshot_id()
    snapshot_path = snapshot_path_for_vm(vm_name, snapshot_id, global_config=global_config)
    disk_path = vm_disk_path(vm_name)
    seed_path = seed_iso_path(vm_name)
    source_was_running = False

    if not disk_path.exists():
        raise FileNotFoundError(f"VM disk was not found for snapshotting: {disk_path}")

    with host_lifecycle_lock("snapshot-create", vm_name=vm_name):
        snapshot_path.mkdir(parents=True, exist_ok=False)

        try:
            source_was_running = stop_vm_domain(vm_name) if vm_exists(vm_name) else False

            snapshot_disk = snapshot_path / f"{vm_name}.qcow2"
            copy_qcow2_image(disk_path, snapshot_disk)

            snapshot_seed = None
            if seed_path.exists():
                snapshot_seed = snapshot_path / f"{vm_name}-seed.iso"
                copy_image_artifact(seed_path, snapshot_seed)

            config_snapshot_path = snapshot_path / "config.yaml"
            copy_local_file(resolved_config_path, config_snapshot_path)

            state_path = state_file_for_vm(vm_name, global_config=global_config)
            if state_path.exists():
                copy_local_file(state_path, snapshot_path / "state.yaml")

            vm_data_dir = Path(
                state.get("vm_data_dir")
                or vm_data_dir_for_config(vm_name, config_data, global_config=global_config)
            )
            if vm_data_dir.exists():
                copy_local_tree(vm_data_dir, snapshot_path / "vm-data")

            admin_private_key = state.get("admin_private_key")
            if admin_private_key:
                admin_key_path = Path(admin_private_key)
                if admin_key_path.exists():
                    copy_local_file(admin_key_path, snapshot_path / "keys" / admin_key_path.name)
                admin_pub_path = Path(str(admin_key_path) + ".pub")
                if admin_pub_path.exists():
                    copy_local_file(admin_pub_path, snapshot_path / "keys" / admin_pub_path.name)

            asset_snapshots = {}
            for asset_name, asset_path in resolved_config_assets(config_data, global_config).items():
                if not asset_path.exists():
                    raise FileNotFoundError(
                        f"Referenced config asset was not found for snapshotting: {asset_path}"
                    )

                snapshot_asset_path = snapshot_path / "assets" / asset_path.name
                copy_local_file(asset_path, snapshot_asset_path)
                asset_snapshots[asset_name] = {
                    "original_path": str(asset_path),
                    "snapshot_path": str(snapshot_asset_path),
                }

            metadata = {
                "snapshot_id": snapshot_id,
                "vm_name": vm_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_was_running": source_was_running,
                "original_paths": {
                    "config_path": str(resolved_config_path),
                    "state_path": str(state_file_for_vm(vm_name, global_config=global_config)),
                    "vm_data_dir": state.get("vm_data_dir"),
                    "admin_private_key": state.get("admin_private_key"),
                },
                "assets": asset_snapshots,
                "files": {
                    "disk": str(snapshot_disk),
                    "seed_iso": str(snapshot_seed) if snapshot_seed is not None else None,
                },
            }
            snapshot_metadata_path(snapshot_path).write_text(
                yaml.safe_dump(metadata, sort_keys=False),
                encoding="utf-8",
            )
            chown_path_to_current_user(snapshot_path)
        except Exception:
            if snapshot_path.exists():
                shutil.rmtree(snapshot_path, ignore_errors=True)
            raise
        finally:
            if source_was_running:
                start_vm_domain(vm_name)

    print(f"Created restore point {snapshot_id} for {vm_name}")


def snapshot_restore(vm_name, snapshot_id):
    """Restore a VM disk and host artifacts from a saved restore point."""
    require_tools()

    metadata = load_snapshot_metadata(vm_name, snapshot_id)
    snapshot_path = snapshot_path_for_vm(vm_name, snapshot_id)
    snapshot_disk = snapshot_path / f"{vm_name}.qcow2"
    snapshot_seed = snapshot_path / f"{vm_name}-seed.iso"
    restored_config_path = snapshot_path / "config.yaml"
    restored_state_path = snapshot_path / "state.yaml"

    if not snapshot_disk.exists():
        raise FileNotFoundError(f"Snapshot disk was not found for {vm_name}: {snapshot_disk}")
    if not restored_config_path.exists():
        raise FileNotFoundError(f"Snapshot config was not found for {vm_name}: {restored_config_path}")
    if not snapshot_seed.exists():
        raise FileNotFoundError(f"Snapshot seed ISO was not found for {vm_name}: {snapshot_seed}")

    with host_lifecycle_lock("snapshot-restore", vm_name=vm_name):
        current_state = load_vm_state(vm_name)
        current_network = merged_vm_network(vm_name, current_state)
        current_ports = current_state.get("ports") or []
        cleanup_vm_runtime_definition(vm_name, current_network, current_ports, remove_storage=False)

        copy_qcow2_image(snapshot_disk, vm_disk_path(vm_name))
        if snapshot_seed.exists():
            copy_image_artifact(snapshot_seed, seed_iso_path(vm_name))

        original_paths = metadata.get("original_paths") or {}
        target_config_path = Path(original_paths.get("config_path") or restored_config_path)
        copy_local_file(restored_config_path, target_config_path)

        if restored_state_path.exists():
            target_state_path = Path(
                original_paths.get("state_path") or state_file_for_vm(vm_name)
            )
            copy_local_file(restored_state_path, target_state_path)

        restored_state = load_vm_state(vm_name)
        restored_vm_data_dir = Path(restored_state.get("vm_data_dir") or "") if restored_state else None
        snapshot_vm_data_dir = snapshot_path / "vm-data"
        if restored_vm_data_dir and snapshot_vm_data_dir.exists():
            copy_local_tree(snapshot_vm_data_dir, restored_vm_data_dir)

        restored_admin_key = restored_state.get("admin_private_key") if restored_state else None
        if restored_admin_key:
            admin_key_path = Path(restored_admin_key)
            snapshot_key_path = snapshot_path / "keys" / admin_key_path.name
            snapshot_pub_path = snapshot_path / "keys" / f"{admin_key_path.name}.pub"
            if snapshot_key_path.exists():
                copy_local_file(snapshot_key_path, admin_key_path)
            if snapshot_pub_path.exists():
                copy_local_file(snapshot_pub_path, Path(str(admin_key_path) + ".pub"))

        for asset_metadata in (metadata.get("assets") or {}).values():
            source_path = Path(asset_metadata["snapshot_path"])
            target_path = Path(asset_metadata["original_path"])
            if source_path.exists():
                copy_local_file(source_path, target_path)

        restored_config = load_config(target_config_path)
        restored_network = build_network_config(vm_name, restored_config.get("network", {}))
        restored_ports = restored_state.get("ports") or restored_config.get("ports") or []
        restored_trust = restored_state.get("trust") or restored_config.get("vm", {}).get(
            "trust",
            "untrusted",
        )
        restored_state["vm_name"] = vm_name
        restored_state["config_path"] = str(target_config_path)
        restored_state["network"] = restored_network
        restored_state["ports"] = restored_ports
        restored_state["trust"] = restored_trust
        save_vm_state(vm_name, restored_state)

        ensure_host_services()
        if restored_network["mode"].startswith("nat"):
            create_nat_network(vm_name, restored_network)

        virt_install(
            vm_name,
            restored_config["vm"],
            build_network_arg(vm_name, restored_network),
            vm_disk_path(vm_name),
            seed_iso_path(vm_name),
            image_settings_for_config(restored_config, global_config=load_global_config())["os_variant"],
        )
        stop_vm_domain(vm_name)
        apply_runtime_networking(
            vm_name,
            restored_network,
            restored_trust,
            restored_ports,
            restored_state,
        )

    print(f"Restored {vm_name} from restore point {snapshot_id}")


def snapshot_delete(vm_name, snapshot_id):
    """Delete a saved restore point."""
    snapshot_path = snapshot_path_for_vm(vm_name, snapshot_id)
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Snapshot not found for {vm_name}: {snapshot_id}")

    shutil.rmtree(snapshot_path)
    print(f"Deleted restore point {snapshot_id} for {vm_name}")


def clone(source_vm_name, config_path):
    """Clone a VM disk into a new VM using a separate saved config."""
    require_tools(["virsh", "virt-install", "qemu-img", "cloud-localds", "ssh-keygen", "virt-customize"])

    target_vm_name = load_config(resolve_config_path(config_path))["vm"]["name"]
    if target_vm_name == source_vm_name:
        raise ValueError("Clone target must use a different vm.name than the source VM")
    if not vm_exists(source_vm_name):
        raise FileNotFoundError(f"Source VM not found: {source_vm_name}")
    if vm_exists(target_vm_name):
        raise RuntimeError(f"Target VM already exists: {target_vm_name}")

    source_disk = vm_disk_path(source_vm_name)
    target_disk = vm_disk_path(target_vm_name)
    if not source_disk.exists():
        raise FileNotFoundError(f"Source VM disk was not found: {source_disk}")
    if target_disk.exists():
        raise RuntimeError(f"Target VM disk already exists: {target_disk}")

    with host_lifecycle_lock("clone", vm_name=target_vm_name):
        definition = prepare_vm_definition(config_path)
        save_vm_state(target_vm_name, definition["state"])
        source_state = load_vm_state(source_vm_name)
        source_config_path = source_state.get("config_path")
        source_vm_user = None
        if source_config_path and Path(source_config_path).exists():
            source_vm_user = load_config(source_config_path).get("vm", {}).get("user")

        source_was_running = False
        try:
            source_was_running = stop_vm_domain(source_vm_name)
            ensure_host_services()

            copy_qcow2_image(source_disk, target_disk)
            prepare_cloned_guest_disk(
                target_disk,
                target_vm_name,
                [source_vm_user, definition["vm_user"]],
            )

            if definition["network"]["mode"].startswith("nat"):
                create_nat_network(target_vm_name, definition["network"])

            seed_iso = render_seed_iso_for_definition(definition)
            virt_install(
                target_vm_name,
                definition["vm"],
                build_network_arg(target_vm_name, definition["network"]),
                target_disk,
                seed_iso,
                definition["image_settings"]["os_variant"],
            )
            apply_runtime_networking(
                target_vm_name,
                definition["network"],
                definition["trust"],
                definition["ports"],
                definition["state"],
            )
        except Exception:
            cleanup_vm_runtime_definition(
                target_vm_name,
                merged_vm_network(target_vm_name, definition["state"]),
                definition["ports"],
                remove_storage=True,
            )
            cleanup_local_vm_artifacts(
                target_vm_name,
                admin_private_key=definition["state"].get("admin_private_key"),
                vm_data_dir=definition["state"].get("vm_data_dir"),
            )
            raise
        finally:
            if source_was_running:
                start_vm_domain(source_vm_name)

    print_create_summary(
        target_vm_name,
        definition["vm_user"],
        definition["trust"],
        definition["network"],
        definition["admin_private_key"],
        definition["ports"],
    )


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
    with host_lifecycle_lock("destroy", vm_name=vm_name):
        state = load_vm_state(vm_name)
        network = merged_vm_network(vm_name, state)
        ports = state.get("ports") or []

        cleanup_vm_runtime_definition(vm_name, network, ports, remove_storage=True)
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

    start_parser = subcommands.add_parser("start")
    start_parser.add_argument("name")

    stop_parser = subcommands.add_parser("stop")
    stop_parser.add_argument("name")

    clone_parser = subcommands.add_parser("clone")
    clone_parser.add_argument("source")
    clone_parser.add_argument("config")

    snapshot_create_parser = subcommands.add_parser("snapshot-create")
    snapshot_create_parser.add_argument("name")

    snapshot_list_parser = subcommands.add_parser("snapshot-list")
    snapshot_list_parser.add_argument("name")

    snapshot_restore_parser = subcommands.add_parser("snapshot-restore")
    snapshot_restore_parser.add_argument("name")
    snapshot_restore_parser.add_argument("snapshot_id")

    snapshot_delete_parser = subcommands.add_parser("snapshot-delete")
    snapshot_delete_parser.add_argument("name")
    snapshot_delete_parser.add_argument("snapshot_id")

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
    elif args.command == "start":
        start(args.name)
    elif args.command == "stop":
        stop(args.name)
    elif args.command == "clone":
        clone(args.source, args.config)
    elif args.command == "snapshot-create":
        snapshot_create(args.name)
    elif args.command == "snapshot-list":
        for snapshot in list_snapshots(args.name):
            print(yaml.safe_dump(snapshot, sort_keys=False).strip())
    elif args.command == "snapshot-restore":
        snapshot_restore(args.name, args.snapshot_id)
    elif args.command == "snapshot-delete":
        snapshot_delete(args.name, args.snapshot_id)
    elif args.command == "ssh-admin":
        ssh_admin(args.name, args.ip)
