import unittest
from unittest.mock import call, patch

from homelab_vm_provisioner import firewall


class CleanupFirewalldVmPolicyTests(unittest.TestCase):
    def test_removes_vm_specific_policy(self):
        with patch.object(firewall, "tool_exists", return_value=True), patch.object(
            firewall, "firewalld_zone_exists", return_value=True
        ), patch.object(
            firewall,
            "find_forward_port_rules_for_vm",
            return_value=[(None, "port=2222:proto=tcp:toaddr=192.168.240.50:toport=22")],
        ), patch.object(
            firewall, "firewalld_zone_is_empty", return_value=True
        ), patch.object(firewall, "run") as run_mock:
            firewall.cleanup_firewalld_vm_policy(
                "demo",
                {
                    "zone": "custom-demo-zone",
                    "cidr": "192.168.240.0/24",
                    "vm_ip": "192.168.240.50",
                },
                [{"host": 2222, "guest": 22, "proto": "tcp"}],
            )

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--remove-forward-port=port=2222:proto=tcp:toaddr=192.168.240.50:toport=22",
                    ],
                    sudo=True,
                    check=False,
                ),
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "custom-demo-zone",
                        "--remove-source",
                        "192.168.240.0/24",
                    ],
                    sudo=True,
                    check=False,
                ),
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "custom-demo-zone",
                        "--remove-rich-rule",
                        'rule family="ipv4" destination address="10.0.0.0/8" reject',
                    ],
                    sudo=True,
                    check=False,
                ),
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "custom-demo-zone",
                        "--remove-rich-rule",
                        'rule family="ipv4" destination address="172.16.0.0/12" reject',
                    ],
                    sudo=True,
                    check=False,
                ),
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "custom-demo-zone",
                        "--remove-rich-rule",
                        'rule family="ipv4" destination address="192.168.0.0/16" reject',
                    ],
                    sudo=True,
                    check=False,
                ),
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "custom-demo-zone",
                        "--remove-rich-rule",
                        'rule family="ipv4" destination address="100.64.0.0/10" reject',
                    ],
                    sudo=True,
                    check=False,
                ),
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "custom-demo-zone",
                        "--remove-rich-rule",
                        'rule family="ipv4" destination address="169.254.0.0/16" reject',
                    ],
                    sudo=True,
                    check=False,
                ),
                call(
                    ["firewall-cmd", "--permanent", "--delete-zone", "custom-demo-zone"],
                    sudo=True,
                    check=False,
                ),
                call(["firewall-cmd", "--reload"], sudo=True, check=False),
            ],
        )
