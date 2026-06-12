"""Helpers for reading user configs and persisting VM state."""

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .constants import (
    BASE_IMG_NAME,
    BASE_IMG_URL,
    DEFAULT_VM_DNS_RESOLVERS,
    GLOBAL_CONFIG_PATH,
    LEGACY_VM_BUILD_DIR,
    OS_VARIANT,
    PROJECT_DIR,
)

DEFAULT_GLOBAL_PATHS = {
    "vm_data_dir": "vm/data",
    "vm_state_dir": "vm/state",
    "user_key_dir": "vm/keys/users",
    "admin_key_dir": "vm/keys/admin",
    "script_dir": "vm/scripts",
    "snapshot_dir": "vm/snapshots",
}

DEFAULT_IMAGE_SETTINGS = {
    "name": BASE_IMG_NAME,
    "url": BASE_IMG_URL,
    "os_variant": OS_VARIANT,
}

DEFAULT_DNS_SETTINGS = {
    "resolvers": DEFAULT_VM_DNS_RESOLVERS,
}


def load_config(path):
    """Load a YAML configuration file.

    Args:
        path: Path to the YAML file.

    Returns:
        dict: Parsed YAML document.
    """
    with open(path, "r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj)


def load_global_config():
    """Load the project-level configuration file when present.

    Returns:
        dict: Parsed project configuration, or an empty dictionary when the
        file does not exist.
    """
    if not GLOBAL_CONFIG_PATH.exists():
        return {}

    with open(GLOBAL_CONFIG_PATH, "r", encoding="utf-8") as file_obj:
        config_data = yaml.safe_load(file_obj) or {}

    if not isinstance(config_data, dict):
        raise ValueError(f"Global config must be a mapping: {GLOBAL_CONFIG_PATH}")

    return config_data


def resolve_config_path(config_path):
    """Resolve a user-supplied config path to an existing file.

    Supports explicit file names, extensionless names, and the ``config/``
    shorthand that maps to ``configs/``.

    Args:
        config_path: User-supplied config path or shorthand.

    Returns:
        Path: Existing config file path.

    Raises:
        FileNotFoundError: If no candidate path exists.
    """
    raw_path = Path(config_path).expanduser()
    candidates = [raw_path]

    if not raw_path.suffix:
        candidates.extend((raw_path.with_suffix(".yaml"), raw_path.with_suffix(".yml")))

    if raw_path.parts and raw_path.parts[0] == "config":
        alt_path = Path("configs", *raw_path.parts[1:])
        candidates.append(alt_path)
        if not alt_path.suffix:
            candidates.extend((alt_path.with_suffix(".yaml"), alt_path.with_suffix(".yml")))

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(f"Missing config file: {config_path}")


def resolve_project_path(path):
    """Resolve a user path relative to the project root when needed.

    Args:
        path: Absolute or project-relative path.

    Returns:
        Path: Absolute path for local artifact storage.
    """
    resolved = Path(path).expanduser()
    if resolved.is_absolute():
        return resolved

    return PROJECT_DIR / resolved


def global_path_settings(global_config=None):
    """Return the effective project-level path settings.

    Args:
        global_config: Optional preloaded global configuration.

    Returns:
        dict[str, Path]: Resolved path settings.
    """
    if global_config is None:
        global_config = load_global_config()

    configured_paths = dict(DEFAULT_GLOBAL_PATHS)
    raw_paths = dict(global_config.get("paths") or {})
    if "provider_key_dir" in raw_paths and "admin_key_dir" not in raw_paths:
        raw_paths["admin_key_dir"] = raw_paths["provider_key_dir"]

    configured_paths.update(raw_paths)
    return {name: resolve_project_path(value) for name, value in configured_paths.items()}


def image_name_from_url(url):
    """Return the filename portion of an image URL.

    Args:
        url: Cloud image download URL.

    Returns:
        str: Filename derived from the URL path.

    Raises:
        ValueError: If the URL does not contain a usable filename.
    """
    image_name = Path(urlparse(url).path).name
    if not image_name:
        raise ValueError(f"Could not derive image name from URL: {url}")

    return image_name


def _apply_image_overrides(base, overrides):
    """Merge one image config layer over another.

    Args:
        base: Existing image settings.
        overrides: New image settings from global or per-VM config.

    Returns:
        dict: Merged image settings.
    """
    merged = dict(base)
    if not overrides:
        return merged

    merged.update(overrides)
    if "url" in overrides and "name" not in overrides:
        merged["name"] = image_name_from_url(merged["url"])

    return merged


def image_settings_for_config(config_data, global_config=None):
    """Return the effective image settings for a VM config.

    Args:
        config_data: Parsed YAML configuration.
        global_config: Optional preloaded global configuration.

    Returns:
        dict: Effective image settings with ``name``, ``url``, and ``os_variant``.
    """
    if global_config is None:
        global_config = load_global_config()

    image_settings = dict(DEFAULT_IMAGE_SETTINGS)
    image_settings = _apply_image_overrides(image_settings, global_config.get("image") or {})
    image_settings = _apply_image_overrides(image_settings, config_data.get("image") or {})
    return image_settings


def _validate_dns_resolvers(resolvers):
    """Validate configured guest DNS resolvers.

    Args:
        resolvers: Sequence of resolver IP strings.

    Returns:
        tuple[str, ...]: Normalized resolver addresses.

    Raises:
        ValueError: If the resolver list is empty, not a sequence, or contains
        an invalid IP address.
    """
    if not isinstance(resolvers, (list, tuple)):
        raise ValueError("dns.resolvers must be a list of IP addresses")
    if not resolvers:
        raise ValueError("dns.resolvers must contain at least one IP address")

    validated = []
    for resolver in resolvers:
        try:
            validated.append(str(ipaddress.ip_address(resolver)))
        except ValueError as exc:
            raise ValueError(f"dns.resolvers contains an invalid IP address: {resolver}") from exc

    return tuple(validated)


def dns_settings_for_config(config_data, global_config=None):
    """Return the effective guest DNS settings for a VM config.

    Args:
        config_data: Parsed YAML configuration.
        global_config: Optional preloaded global configuration.

    Returns:
        dict: Effective DNS settings.
    """
    if global_config is None:
        global_config = load_global_config()

    dns_settings = dict(DEFAULT_DNS_SETTINGS)
    dns_settings.update(global_config.get("dns") or {})
    dns_settings.update(config_data.get("dns") or {})
    dns_settings["resolvers"] = _validate_dns_resolvers(dns_settings["resolvers"])
    return dns_settings


def default_vm_data_root(global_config=None):
    """Return the default root directory for per-VM local artifacts."""
    return global_path_settings(global_config)["vm_data_dir"]


def default_vm_state_root(global_config=None):
    """Return the default root directory for VM state files."""
    return global_path_settings(global_config)["vm_state_dir"]


def default_user_key_dir(global_config=None):
    """Return the default directory for user-managed SSH public keys."""
    return global_path_settings(global_config)["user_key_dir"]


def default_admin_key_dir(global_config=None):
    """Return the default directory for generated admin SSH keys."""
    return global_path_settings(global_config)["admin_key_dir"]


def default_script_dir(global_config=None):
    """Return the default directory for saved setup scripts."""
    return global_path_settings(global_config)["script_dir"]


def default_snapshot_root(global_config=None):
    """Return the default directory for saved VM restore points."""
    return global_path_settings(global_config)["snapshot_dir"]


def default_vm_data_dir(vm_name, global_config=None):
    """Return the default local artifact directory for a VM.

    Args:
        vm_name: VM name.

    Returns:
        Path: Directory used for generated cloud-init files and other local VM
        artifacts.
    """
    return default_vm_data_root(global_config) / vm_name


def legacy_vm_data_dir(vm_name):
    """Return the legacy per-VM build directory.

    Args:
        vm_name: VM name.

    Returns:
        Path: Old ``.build/<vm>`` directory path.
    """
    return LEGACY_VM_BUILD_DIR / vm_name


def vm_data_dir_for_config(vm_name, config_data, global_config=None):
    """Return the local artifact directory for a VM config.

    Args:
        vm_name: VM name.
        config_data: Parsed YAML configuration.

    Returns:
        Path: Directory used for generated cloud-init files and admin keys.
    """
    paths = config_data.get("paths") or {}
    configured_dir = paths.get("vm_data_dir")
    if configured_dir:
        return resolve_project_path(configured_dir)

    return default_vm_data_dir(vm_name, global_config=global_config)


def resolve_user_key_path(path, global_config=None):
    """Resolve a tenant SSH public key path.

    Args:
        path: User-supplied key path.
        global_config: Optional preloaded global configuration.

    Returns:
        Path: Existing key path when found, or the most likely intended path.
    """
    raw_path = Path(path).expanduser()
    if raw_path.is_absolute():
        return raw_path

    candidates = [raw_path, resolve_project_path(raw_path)]
    user_key_candidate = default_user_key_dir(global_config) / raw_path
    if user_key_candidate not in candidates:
        candidates.append(user_key_candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    if len(raw_path.parts) == 1:
        return user_key_candidate

    return resolve_project_path(raw_path)


def resolve_setup_script_path(path, global_config=None):
    """Resolve a setup script path.

    Args:
        path: User-supplied script path.
        global_config: Optional preloaded global configuration.

    Returns:
        Path: Existing script path when found, or the most likely intended path.
    """
    raw_path = Path(path).expanduser()
    if raw_path.is_absolute():
        return raw_path

    candidates = [raw_path, resolve_project_path(raw_path)]
    script_candidate = default_script_dir(global_config) / raw_path
    if script_candidate not in candidates:
        candidates.append(script_candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    if len(raw_path.parts) == 1:
        return script_candidate

    return resolve_project_path(raw_path)


def state_file_for_vm(vm_name, global_config=None):
    """Return the persisted state file path for a VM.

    Args:
        vm_name: VM name.

    Returns:
        Path: YAML state file path inside ``vm/state/``.
    """
    return default_vm_state_root(global_config) / f"{vm_name}.yaml"


def legacy_state_file_for_vm(vm_name):
    """Return the legacy persisted state path for a VM.

    Args:
        vm_name: VM name.

    Returns:
        Path: Old state file path inside ``.build/<vm>/state.yaml``.
    """
    return legacy_vm_data_dir(vm_name) / "state.yaml"


def save_vm_state(vm_name, state):
    """Persist teardown metadata for a VM.

    Args:
        vm_name: VM name.
        state: Serializable state dictionary.
    """
    state_root = default_vm_state_root()
    state_root.mkdir(parents=True, exist_ok=True)

    with open(state_file_for_vm(vm_name), "w", encoding="utf-8") as file_obj:
        yaml.safe_dump(state, file_obj, sort_keys=False)


def delete_vm_state(vm_name):
    """Remove persisted teardown metadata for a VM.

    Args:
        vm_name: VM name.
    """
    for path in (state_file_for_vm(vm_name), legacy_state_file_for_vm(vm_name)):
        if path.exists():
            path.unlink()


def load_vm_state(vm_name):
    """Load persisted teardown metadata for a VM.

    Args:
        vm_name: VM name.

    Returns:
        dict: Stored state, or an empty dictionary when no state file exists.
    """
    state_path = state_file_for_vm(vm_name)
    if not state_path.exists():
        state_path = legacy_state_file_for_vm(vm_name)
        if not state_path.exists():
            return {}

    with open(state_path, "r", encoding="utf-8") as file_obj:
        state = yaml.safe_load(file_obj) or {}

    if "admin_private_key" not in state and "provider_private_key" in state:
        state["admin_private_key"] = state["provider_private_key"]

    if state_path == legacy_state_file_for_vm(vm_name):
        state.setdefault("vm_data_dir", str(legacy_vm_data_dir(vm_name)))

    return state
