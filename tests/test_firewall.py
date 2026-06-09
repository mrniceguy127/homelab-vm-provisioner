import unittest
from unittest.mock import call, patch

from homelab_vm_provisioner import firewall


class ForwardPortSpecTests(unittest.TestCase):
    def test_builds_firewalld_forward_port_spec(self):
        self.assertEqual(
            firewall.forward_port_spec(
                {"host": 2222, "guest": 22, "proto": "tcp"},
                "192.168.240.50",
            ),
            "port=2222:proto=tcp:toaddr=192.168.240.50:toport=22",
        )


class ZoneLookupTests(unittest.TestCase):
    def test_firewalld_zone_exists_checks_permanent_zones(self):
        with patch.object(firewall, "capture_or_none", return_value="public demo-zone"):
            self.assertTrue(firewall.firewalld_zone_exists("demo-zone"))
            self.assertFalse(firewall.firewalld_zone_exists("missing-zone"))

    def test_firewalld_zone_for_cidr_returns_matching_zone(self):
        with patch.object(
            firewall,
            "capture_or_none",
            side_effect=["public demo-zone", "10.0.0.0/8", "192.168.240.0/24"],
        ):
            self.assertEqual(
                firewall.firewalld_zone_for_cidr("192.168.240.0/24", preferred_zone="public"),
                "demo-zone",
            )

    def test_list_zone_forward_ports_splits_output(self):
        with patch.object(
            firewall,
            "capture_or_none",
            return_value=(
                "port=2222:proto=tcp:toaddr=1.2.3.4:toport=22 "
                "port=8080:proto=tcp:toaddr=1.2.3.4:toport=80"
            ),
        ):
            self.assertEqual(
                firewall.list_zone_forward_ports("demo-zone"),
                [
                    "port=2222:proto=tcp:toaddr=1.2.3.4:toport=22",
                    "port=8080:proto=tcp:toaddr=1.2.3.4:toport=80",
                ],
            )

    def test_find_forward_port_rules_for_vm_filters_by_ip(self):
        with patch.object(
            firewall,
            "capture_or_none",
            return_value="public demo-zone",
        ), patch.object(
            firewall,
            "list_zone_forward_ports",
            side_effect=[
                ["port=2222:proto=tcp:toaddr=192.168.240.50:toport=22"],
                ["port=2222:proto=tcp:toaddr=192.168.240.50:toport=22"],
                ["port=8080:proto=tcp:toaddr=192.168.240.51:toport=80"],
            ],
        ):
            self.assertEqual(
                firewall.find_forward_port_rules_for_vm("192.168.240.50"),
                [
                    (None, "port=2222:proto=tcp:toaddr=192.168.240.50:toport=22"),
                    ("public", "port=2222:proto=tcp:toaddr=192.168.240.50:toport=22"),
                ],
            )

    def test_firewalld_zone_is_empty_returns_false_on_any_data(self):
        with patch.object(
            firewall,
            "capture_or_none",
            side_effect=["", "interface0"],
        ):
            self.assertFalse(firewall.firewalld_zone_is_empty("demo-zone"))

    def test_firewalld_zone_is_empty_returns_true_for_empty_results(self):
        with patch.object(firewall, "capture_or_none", return_value=""):
            self.assertTrue(firewall.firewalld_zone_is_empty("demo-zone"))


class ApplyFirewalldNatPolicyTests(unittest.TestCase):
    def test_applies_zone_rules_and_reload(self):
        with patch.object(firewall, "capture", return_value="public"), patch.object(
            firewall, "run"
        ) as run_mock:
            zone_created = firewall.apply_firewalld_nat_policy(
                {
                    "zone": "demo-zone",
                    "cidr": "192.168.240.0/24",
                    "vm_ip": "192.168.240.50",
                },
                "untrusted",
                [{"host": 2222, "guest": 22, "proto": "tcp"}],
            )

        self.assertTrue(zone_created)
        self.assertEqual(
            run_mock.call_args_list[0],
            call(["firewall-cmd", "--permanent", "--new-zone", "demo-zone"], sudo=True),
        )
        self.assertEqual(run_mock.call_args_list[-1], call(["firewall-cmd", "--reload"], sudo=True))


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

    def test_returns_early_when_firewalld_is_unavailable(self):
        with patch.object(firewall, "tool_exists", return_value=False), patch.object(
            firewall, "run"
        ) as run_mock:
            firewall.cleanup_firewalld_vm_policy("demo", {}, [])

        run_mock.assert_not_called()
