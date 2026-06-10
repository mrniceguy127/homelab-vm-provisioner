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

    def test_builds_direct_forward_rule_args(self):
        self.assertEqual(
            firewall.direct_forward_rule_args(
                {"host": 2222, "guest": 22, "proto": "tcp"},
                "192.168.240.50",
            ),
            [
                "ipv4",
                "filter",
                "FORWARD",
                "-1000",
                "-p",
                "tcp",
                "-d",
                "192.168.240.50",
                "--dport",
                "22",
                "-j",
                "ACCEPT",
            ],
        )

    def test_direct_forward_ports_deduplicate_configured_ports(self):
        self.assertEqual(
            firewall.direct_forward_ports(
                [
                    {"host": 2222, "guest": 22, "proto": "tcp"},
                    {"host": 8080, "guest": 80, "proto": "tcp"},
                    {"host": 51153, "guest": 25565, "proto": "udp"},
                ]
            ),
            [
                {"guest": "22", "proto": "tcp"},
                {"guest": "80", "proto": "tcp"},
                {"guest": "25565", "proto": "udp"},
            ],
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

    def test_firewalld_zone_for_cidr_returns_none_when_zones_cannot_be_listed(self):
        with patch.object(firewall, "capture_or_none", return_value=None):
            self.assertIsNone(firewall.firewalld_zone_for_cidr("192.168.240.0/24"))

    def test_firewalld_zone_for_cidr_returns_none_when_no_zone_matches(self):
        with patch.object(
            firewall,
            "capture_or_none",
            side_effect=["public demo-zone", "10.0.0.0/8", "172.16.0.0/12"],
        ):
            self.assertIsNone(firewall.firewalld_zone_for_cidr("192.168.240.0/24"))

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

    def test_list_zone_forward_ports_returns_empty_when_unset(self):
        with patch.object(firewall, "capture_or_none", return_value=None):
            self.assertEqual(firewall.list_zone_forward_ports(), [])

    def test_list_direct_rules_splits_output_lines(self):
        with patch.object(
            firewall,
            "capture_or_none",
            return_value=(
                "ipv4 filter FORWARD -1000 -p tcp -d 192.168.240.50 --dport 22 -j ACCEPT\n"
                "ipv4 filter FORWARD -1000 -p udp -d 192.168.240.50 --dport 25565 -j ACCEPT"
            ),
        ):
            self.assertEqual(
                firewall.list_direct_rules(),
                [
                    [
                        "ipv4",
                        "filter",
                        "FORWARD",
                        "-1000",
                        "-p",
                        "tcp",
                        "-d",
                        "192.168.240.50",
                        "--dport",
                        "22",
                        "-j",
                        "ACCEPT",
                    ],
                    [
                        "ipv4",
                        "filter",
                        "FORWARD",
                        "-1000",
                        "-p",
                        "udp",
                        "-d",
                        "192.168.240.50",
                        "--dport",
                        "25565",
                        "-j",
                        "ACCEPT",
                    ],
                ],
            )

    def test_find_direct_forward_rules_for_vm_filters_by_destination(self):
        with patch.object(
            firewall,
            "list_direct_rules",
            return_value=[
                [
                    "ipv4",
                    "filter",
                    "FORWARD",
                    "-1000",
                    "-p",
                    "tcp",
                    "-d",
                    "192.168.240.50",
                    "--dport",
                    "22",
                    "-j",
                    "ACCEPT",
                ],
                ["ipv4", "filter", "INPUT", "-1000", "-j", "ACCEPT"],
                [
                    "ipv4",
                    "filter",
                    "FORWARD",
                    "-1000",
                    "-p",
                    "tcp",
                    "-d",
                    "192.168.240.51",
                    "--dport",
                    "80",
                    "-j",
                    "ACCEPT",
                ],
            ],
        ):
            self.assertEqual(
                firewall.find_direct_forward_rules_for_vm("192.168.240.50"),
                [
                    [
                        "ipv4",
                        "filter",
                        "FORWARD",
                        "-1000",
                        "-p",
                        "tcp",
                        "-d",
                        "192.168.240.50",
                        "--dport",
                        "22",
                        "-j",
                        "ACCEPT",
                    ]
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

    def test_find_forward_port_rules_for_vm_skips_duplicate_zone_rule_pairs(self):
        with patch.object(
            firewall,
            "capture_or_none",
            return_value="demo-zone demo-zone",
        ), patch.object(
            firewall,
            "list_zone_forward_ports",
            side_effect=[
                [],
                ["port=2222:proto=tcp:toaddr=192.168.240.50:toport=22"],
                ["port=2222:proto=tcp:toaddr=192.168.240.50:toport=22"],
            ],
        ):
            self.assertEqual(
                firewall.find_forward_port_rules_for_vm("192.168.240.50"),
                [("demo-zone", "port=2222:proto=tcp:toaddr=192.168.240.50:toport=22")],
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
        self.assertEqual(
            run_mock.call_args_list[1],
            call(
                ["firewall-cmd", "--permanent", "--zone", "demo-zone", "--set-target", "ACCEPT"],
                sudo=True,
            ),
        )
        self.assertIn(
            call(
                [
                    "firewall-cmd",
                    "--permanent",
                    "--direct",
                    "--add-rule",
                    "ipv4",
                    "filter",
                    "FORWARD",
                    "-1000",
                    "-p",
                    "tcp",
                    "-d",
                    "192.168.240.50",
                    "--dport",
                    "22",
                    "-j",
                    "ACCEPT",
                ],
                sudo=True,
            ),
            run_mock.call_args_list,
        )
        self.assertEqual(run_mock.call_args_list[-1], call(["firewall-cmd", "--reload"], sudo=True))

    def test_trusted_vm_uses_existing_zone_without_rich_rules(self):
        with patch.object(firewall, "capture", return_value="demo-zone public"), patch.object(
            firewall, "run"
        ) as run_mock:
            zone_created = firewall.apply_firewalld_nat_policy(
                {
                    "zone": "demo-zone",
                    "cidr": "192.168.240.0/24",
                    "vm_ip": "192.168.240.50",
                },
                "trusted",
                [],
            )

        self.assertFalse(zone_created)
        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "demo-zone",
                        "--set-target",
                        "ACCEPT",
                    ],
                    sudo=True,
                ),
                call(
                    [
                        "firewall-cmd",
                        "--permanent",
                        "--zone",
                        "demo-zone",
                        "--add-source",
                        "192.168.240.0/24",
                    ],
                    sudo=True,
                ),
                call(["firewall-cmd", "--reload"], sudo=True),
            ],
        )


class RemoveForwardPortRuleTests(unittest.TestCase):
    def test_remove_forward_port_rule_uses_zone_when_provided(self):
        with patch.object(firewall, "run") as run_mock:
            firewall.remove_forward_port_rule(
                "port=2222:proto=tcp:toaddr=1.2.3.4:toport=22",
                zone="demo-zone",
            )

        run_mock.assert_called_once_with(
            [
                "firewall-cmd",
                "--permanent",
                "--zone",
                "demo-zone",
                "--remove-forward-port=port=2222:proto=tcp:toaddr=1.2.3.4:toport=22",
            ],
            sudo=True,
            check=False,
        )

    def test_remove_direct_rule_uses_direct_remove(self):
        with patch.object(firewall, "run") as run_mock:
            firewall.remove_direct_rule(
                [
                    "ipv4",
                    "filter",
                    "FORWARD",
                    "-1000",
                    "-p",
                    "tcp",
                    "-d",
                    "192.168.240.50",
                    "--dport",
                    "22",
                    "-j",
                    "ACCEPT",
                ]
            )

        run_mock.assert_called_once_with(
            [
                "firewall-cmd",
                "--permanent",
                "--direct",
                "--remove-rule",
                "ipv4",
                "filter",
                "FORWARD",
                "-1000",
                "-p",
                "tcp",
                "-d",
                "192.168.240.50",
                "--dport",
                "22",
                "-j",
                "ACCEPT",
            ],
            sudo=True,
            check=False,
        )


class CleanupFirewalldVmPolicyTests(unittest.TestCase):
    def test_removes_vm_specific_policy(self):
        with patch.object(firewall, "tool_exists", return_value=True), patch.object(
            firewall, "firewalld_zone_exists", return_value=True
        ), patch.object(
            firewall,
            "find_direct_forward_rules_for_vm",
            return_value=[
                [
                    "ipv4",
                    "filter",
                    "FORWARD",
                    "-1000",
                    "-p",
                    "tcp",
                    "-d",
                    "192.168.240.50",
                    "--dport",
                    "22",
                    "-j",
                    "ACCEPT",
                ]
            ],
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
                        "--direct",
                        "--remove-rule",
                        "ipv4",
                        "filter",
                        "FORWARD",
                        "-1000",
                        "-p",
                        "tcp",
                        "-d",
                        "192.168.240.50",
                        "--dport",
                        "22",
                        "-j",
                        "ACCEPT",
                    ],
                    sudo=True,
                    check=False,
                ),
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

    def test_uses_port_fallback_and_cidr_lookup_when_no_rule_scan_matches(self):
        with patch.object(firewall, "tool_exists", return_value=True), patch.object(
            firewall, "firewalld_zone_exists", return_value=False
        ), patch.object(
            firewall, "firewalld_zone_for_cidr", return_value="demo-zone"
        ), patch.object(
            firewall, "find_direct_forward_rules_for_vm", return_value=[]
        ), patch.object(
            firewall, "find_forward_port_rules_for_vm", return_value=[]
        ), patch.object(
            firewall, "remove_forward_port_rule"
        ) as remove_rule_mock, patch.object(
            firewall, "remove_direct_rule"
        ) as remove_direct_rule_mock, patch.object(
            firewall, "firewalld_zone_is_empty", return_value=False
        ), patch.object(firewall, "run") as run_mock:
            firewall.cleanup_firewalld_vm_policy(
                "demo",
                {
                    "zone": "demo-zone",
                    "cidr": "192.168.240.0/24",
                    "vm_ip": "192.168.240.50",
                },
                [{"host": 2222, "guest": 22, "proto": "tcp"}],
            )

        remove_direct_rule_mock.assert_called_once_with(
            [
                "ipv4",
                "filter",
                "FORWARD",
                "-1000",
                "-p",
                "tcp",
                "-d",
                "192.168.240.50",
                "--dport",
                "22",
                "-j",
                "ACCEPT",
            ]
        )
        remove_rule_mock.assert_called_once_with(
            "port=2222:proto=tcp:toaddr=192.168.240.50:toport=22"
        )
        self.assertEqual(
            run_mock.call_args_list[-1],
            call(["firewall-cmd", "--reload"], sudo=True, check=False),
        )

    def test_fallback_direct_rules_do_not_run_without_configured_ports(self):
        with patch.object(firewall, "tool_exists", return_value=True), patch.object(
            firewall, "firewalld_zone_exists", return_value=False
        ), patch.object(
            firewall, "firewalld_zone_for_cidr", return_value="demo-zone"
        ), patch.object(
            firewall, "find_direct_forward_rules_for_vm", return_value=[]
        ), patch.object(
            firewall, "find_forward_port_rules_for_vm", return_value=[]
        ), patch.object(
            firewall, "remove_direct_rule"
        ) as remove_direct_rule_mock, patch.object(
            firewall, "remove_forward_port_rule"
        ) as remove_forward_rule_mock, patch.object(
            firewall, "firewalld_zone_is_empty", return_value=False
        ), patch.object(firewall, "run"):
            firewall.cleanup_firewalld_vm_policy(
                "demo",
                {
                    "zone": "demo-zone",
                    "cidr": "192.168.240.0/24",
                    "vm_ip": "192.168.240.50",
                },
                [],
            )

        remove_direct_rule_mock.assert_not_called()
        remove_forward_rule_mock.assert_not_called()

    def test_skips_reload_when_no_cleanup_is_needed(self):
        with patch.object(firewall, "tool_exists", return_value=True), patch.object(
            firewall, "firewalld_zone_exists", return_value=False
        ), patch.object(
            firewall, "find_forward_port_rules_for_vm"
        ) as find_rules_mock, patch.object(
            firewall, "run"
        ) as run_mock:
            firewall.cleanup_firewalld_vm_policy("demo", {}, [])

        find_rules_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_zone_cleanup_without_cidr_still_removes_rich_rules_and_reloads(self):
        with patch.object(firewall, "tool_exists", return_value=True), patch.object(
            firewall, "firewalld_zone_exists", return_value=True
        ), patch.object(
            firewall, "find_forward_port_rules_for_vm", return_value=[]
        ), patch.object(
            firewall, "firewalld_zone_is_empty", return_value=False
        ), patch.object(firewall, "run") as run_mock:
            firewall.cleanup_firewalld_vm_policy(
                "demo",
                {"zone": "demo-zone", "vm_ip": "192.168.240.50"},
                [],
            )

        self.assertEqual(
            run_mock.call_args_list[-1],
            call(["firewall-cmd", "--reload"], sudo=True, check=False),
        )
