Architecture
============

Package Layout
--------------

The Python package is split by responsibility:

+--------------------------------+---------------------------------------------+
| Module                         | Responsibility                              |
+================================+=============================================+
| ``homelab_vm_provisioner.cli`` | CLI parsing and high-level orchestration    |
+--------------------------------+---------------------------------------------+
| ``config``                     | Config loading and saved VM state           |
+--------------------------------+---------------------------------------------+
| ``network``                    | Network selection and libvirt discovery     |
+--------------------------------+---------------------------------------------+
| ``provision``                  | Template rendering and libvirt provisioning |
+--------------------------------+---------------------------------------------+
| ``firewall``                   | Firewalld rule management                   |
+--------------------------------+---------------------------------------------+
| ``system``                     | Shared subprocess helpers                   |
+--------------------------------+---------------------------------------------+

Generated Artifacts
-------------------

- ``.build/<vm>/`` stores rendered cloud-init files and teardown state.
- ``provider-keys/`` stores per-VM admin SSH keypairs.
- ``docs/_build/html/`` stores generated HTML documentation.
