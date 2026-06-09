"""Helpers for reading user configs and persisting VM state."""

from pathlib import Path

import yaml

from .constants import BUILD_DIR


def load_config(path):
    """Load a YAML configuration file.

    Args:
        path: Path to the YAML file.

    Returns:
        dict: Parsed YAML document.
    """
    with open(path, "r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj)


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


def build_dir_for_vm(vm_name):
    """Return the build artifact directory for a VM.

    Args:
        vm_name: VM name.

    Returns:
        Path: Directory used for generated cloud-init and state files.
    """
    return BUILD_DIR / vm_name


def state_file_for_vm(vm_name):
    """Return the persisted state file path for a VM.

    Args:
        vm_name: VM name.

    Returns:
        Path: YAML state file path inside the VM build directory.
    """
    return build_dir_for_vm(vm_name) / "state.yaml"


def save_vm_state(vm_name, state):
    """Persist teardown metadata for a VM.

    Args:
        vm_name: VM name.
        state: Serializable state dictionary.
    """
    build_dir = build_dir_for_vm(vm_name)
    build_dir.mkdir(parents=True, exist_ok=True)

    with open(state_file_for_vm(vm_name), "w", encoding="utf-8") as file_obj:
        yaml.safe_dump(state, file_obj, sort_keys=False)


def load_vm_state(vm_name):
    """Load persisted teardown metadata for a VM.

    Args:
        vm_name: VM name.

    Returns:
        dict: Stored state, or an empty dictionary when no state file exists.
    """
    state_path = state_file_for_vm(vm_name)
    if not state_path.exists():
        return {}

    with open(state_path, "r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj) or {}
