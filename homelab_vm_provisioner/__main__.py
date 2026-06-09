"""Module entrypoint for ``python -m homelab_vm_provisioner``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
