import textwrap
import unittest
from unittest.mock import call, patch

from helpers import completed_process

from homelab_vm_provisioner import network


class ParseIpv4FromDomifaddrTests(unittest.TestCase):
    def test_returns_ipv4_address(self):
        output = textwrap.dedent(
            """\
            Name       MAC address          Protocol     Address
            -------------------------------------------------------------------------------
            vnet0      52:54:00:aa:bb:cc    ipv4         192.168.122.50/24
            """
        )

        self.assertEqual(network.parse_ipv4_from_domifaddr(output), "192.168.122.50")

    def test_ignores_non_ipv4_and_invalid_rows(self):
        output = textwrap.dedent(
            """\
            Name       MAC address          Protocol     Address
            -------------------------------------------------------------------------------
            vnet0      52:54:00:aa:bb:cc    ipv6         fd00::50/64
            broken     row
            """
        )

        self.assertIsNone(network.parse_ipv4_from_domifaddr(output))


class ResolveVmIpv4Tests(unittest.TestCase):
    def test_uses_first_source_with_ipv4_address(self):
        output = textwrap.dedent(
            """\
            Name       MAC address          Protocol     Address
            -------------------------------------------------------------------------------
            vnet0      52:54:00:aa:bb:cc    ipv4         192.168.122.77/24
            """
        )

        with patch.object(
            network.subprocess,
            "run",
            side_effect=[completed_process(returncode=1), completed_process(stdout=output)],
        ) as run_mock:
            self.assertEqual(network.resolve_vm_ipv4("demo"), ("192.168.122.77", "agent"))

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    ["sudo", "virsh", "domifaddr", "demo", "--source", "lease"],
                    stdout=network.subprocess.PIPE,
                    stderr=network.subprocess.DEVNULL,
                    text=True,
                ),
                call(
                    ["sudo", "virsh", "domifaddr", "demo", "--source", "agent"],
                    stdout=network.subprocess.PIPE,
                    stderr=network.subprocess.DEVNULL,
                    text=True,
                ),
            ],
        )

    def test_returns_none_when_no_source_has_ipv4(self):
        with patch.object(
            network.subprocess,
            "run",
            side_effect=[
                completed_process(stdout=""),
                completed_process(stdout=""),
                completed_process(stdout=""),
            ],
        ):
            self.assertEqual(network.resolve_vm_ipv4("demo"), (None, None))


class ParseNetworkFromXmlTests(unittest.TestCase):
    def test_returns_nat_network_details_for_matching_vm(self):
        xml_text = textwrap.dedent(
            """\
            <network>
              <name>custom-nat-vm-net</name>
              <forward mode='nat'/>
              <bridge name='virbr-demo' stp='on' delay='0'/>
              <ip address='192.168.240.1' netmask='255.255.255.0'>
                <dhcp>
                  <host mac='52:54:00:aa:bb:cc' name='demo' ip='192.168.240.50'/>
                </dhcp>
              </ip>
            </network>
            """
        )

        self.assertEqual(
            network.parse_network_from_xml(xml_text, "demo"),
            {
                "mode": "nat",
                "name": "custom-nat-vm-net",
                "gateway": "192.168.240.1",
                "cidr": "192.168.240.0/24",
                "vm_ip": "192.168.240.50",
                "mac": "52:54:00:aa:bb:cc",
            },
        )

    def test_returns_none_when_vm_is_not_present(self):
        self.assertIsNone(
            network.parse_network_from_xml("<network><name>demo-net</name></network>", "demo")
        )


class RoutesAndLibvirtDiscoveryTests(unittest.TestCase):
    def test_get_existing_routes_text_returns_command_output(self):
        with patch.object(
            network.subprocess,
            "run",
            return_value=completed_process(stdout="default\n"),
        ):
            self.assertEqual(network.get_existing_routes_text(), "default\n")

    def test_get_existing_virsh_networks_text_concatenates_network_xml(self):
        with patch.object(
            network.subprocess,
            "run",
            side_effect=[
                completed_process(stdout="net-a\nnet-b\n"),
                completed_process(stdout="<network>a</network>"),
                completed_process(stdout="<network>b</network>"),
            ],
        ):
            self.assertEqual(
                network.get_existing_virsh_networks_text(),
                "<network>a</network>\n<network>b</network>\n",
            )

    def test_list_virsh_network_names_filters_blank_lines(self):
        with patch.object(
            network.subprocess,
            "run",
            return_value=completed_process(stdout="net-a\n\nnet-b\n"),
        ):
            self.assertEqual(network.list_virsh_network_names(), ["net-a", "net-b"])

    def test_discover_vm_network_returns_matching_network(self):
        with patch.object(
            network,
            "list_virsh_network_names",
            return_value=["net-a", "net-b"],
        ), patch.object(
            network,
            "capture_or_none",
            side_effect=["<network />", "<xml>match</xml>"],
        ), patch.object(
            network,
            "parse_network_from_xml",
            side_effect=[None, {"name": "net-b", "vm_ip": "192.168.122.50"}],
        ):
            self.assertEqual(
                network.discover_vm_network("demo"),
                {"name": "net-b", "vm_ip": "192.168.122.50"},
            )


class PickFreeSubnetTests(unittest.TestCase):
    def test_returns_first_available_prefix(self):
        with patch.object(network, "subnet_appears_used", side_effect=[True, True, False]):
            self.assertEqual(
                network.pick_free_subnet(),
                {
                    "prefix": "192.168.102",
                    "cidr": "192.168.102.0/24",
                    "gateway": "192.168.102.1",
                    "vm_ip": "192.168.102.50",
                    "dhcp_start": "192.168.102.50",
                    "dhcp_end": "192.168.102.99",
                },
            )

    def test_raises_when_no_free_subnet_exists(self):
        with patch.object(network, "subnet_appears_used", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Could not find free"):
                network.pick_free_subnet()
