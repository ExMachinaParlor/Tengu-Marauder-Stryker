"""
Unit tests for ScannerService (Control/services/scanner.py).

No real subprocesses are spawned; _run() is patched to return canned
output so tests are fast and offline-safe.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# ── Path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Control"))

for _mod in ["fusion_hat", "fusion_hat.motor"]:
    sys.modules.setdefault(_mod, MagicMock())

from services.scanner import ScannerService  # noqa: E402
import services.scanner as _scanner_mod      # noqa: E402


# ── Static parser unit tests ─────────────────────────────────────────────────

class TestArpScanParser(unittest.TestCase):

    ARP_OUTPUT = (
        "Interface: eth0, datalink type: EN10MB (Ethernet)\n"
        "192.168.1.1\t11:22:33:44:55:66\tNetgear, Inc.\n"
        "192.168.1.42\tAA:BB:CC:DD:EE:FF\tRaspberry Pi Foundation\n"
        "4 packets received...\n"
    )

    def test_parses_ip_and_mac(self):
        result = ScannerService._parse_arp_scan(self.ARP_OUTPUT)
        ips = [h["ip"] for h in result]
        self.assertIn("192.168.1.1", ips)
        self.assertIn("192.168.1.42", ips)

    def test_parses_vendor(self):
        result = ScannerService._parse_arp_scan(self.ARP_OUTPUT)
        vendors = {h["ip"]: h["vendor"] for h in result}
        self.assertEqual(vendors["192.168.1.1"], "Netgear, Inc.")

    def test_ignores_non_ip_lines(self):
        result = ScannerService._parse_arp_scan(self.ARP_OUTPUT)
        self.assertEqual(len(result), 2)

    def test_empty_output_returns_empty_list(self):
        self.assertEqual(ScannerService._parse_arp_scan(""), [])


class TestNmapParser(unittest.TestCase):

    NMAP_IP_ONLY = (
        "Nmap scan report for 10.0.0.1\n"
        "Host is up (0.001s latency).\n"
        "Nmap scan report for 10.0.0.2\n"
        "Host is up (0.002s latency).\n"
    )

    NMAP_WITH_HOSTNAME = (
        "Nmap scan report for router.local (192.168.0.1)\n"
        "Host is up (0.001s latency).\n"
    )

    def test_parses_bare_ip(self):
        result = ScannerService._parse_nmap(self.NMAP_IP_ONLY)
        ips = [h["ip"] for h in result]
        self.assertIn("10.0.0.1", ips)
        self.assertIn("10.0.0.2", ips)

    def test_parses_ip_from_hostname_format(self):
        result = ScannerService._parse_nmap(self.NMAP_WITH_HOSTNAME)
        self.assertEqual(result[0]["ip"], "192.168.0.1")

    def test_ignores_hosts_not_marked_up(self):
        out = "Nmap scan report for 10.0.0.5\n"  # no "Host is up" line
        result = ScannerService._parse_nmap(out)
        self.assertEqual(result, [])


class TestHcitoolParser(unittest.TestCase):

    HCITOOL_OUTPUT = (
        "Scanning ...\n"
        "    AA:BB:CC:11:22:33\tMy Phone\n"
        "    DD:EE:FF:44:55:66\t\n"
    )

    def test_parses_mac_and_name(self):
        result = ScannerService._parse_hcitool_scan(self.HCITOOL_OUTPUT)
        self.assertEqual(result[0]["mac"], "AA:BB:CC:11:22:33")
        self.assertEqual(result[0]["name"], "My Phone")

    def test_unknown_name_for_empty_name(self):
        result = ScannerService._parse_hcitool_scan(self.HCITOOL_OUTPUT)
        self.assertEqual(result[1]["name"], "unknown")

    def test_mac_uppercased(self):
        out = "    aa:bb:cc:11:22:33\tDevice\n"
        result = ScannerService._parse_hcitool_scan(out)
        self.assertEqual(result[0]["mac"], "AA:BB:CC:11:22:33")


class TestIwScanParser(unittest.TestCase):

    IW_OUTPUT = (
        "BSS aa:bb:cc:dd:ee:ff(on wlan0)\n"
        "    SSID: HomeNetwork\n"
        "    DS Parameter set: channel 6\n"
        "    signal: -62.00 dBm\n"
        "    RSN:\n"
        "BSS 11:22:33:44:55:66(on wlan0)\n"
        "    SSID: OpenNet\n"
        "    signal: -80.00 dBm\n"
    )

    def test_parses_two_aps(self):
        result = ScannerService._parse_iw_scan(self.IW_OUTPUT)
        self.assertEqual(len(result), 2)

    def test_ssid_and_channel_parsed(self):
        result = ScannerService._parse_iw_scan(self.IW_OUTPUT)
        self.assertEqual(result[0]["ssid"], "HomeNetwork")
        self.assertEqual(result[0]["channel"], "6")

    def test_wpa2_detected(self):
        result = ScannerService._parse_iw_scan(self.IW_OUTPUT)
        self.assertEqual(result[0]["encryption"], "WPA2")

    def test_open_network_detected(self):
        result = ScannerService._parse_iw_scan(self.IW_OUTPUT)
        self.assertEqual(result[1]["encryption"], "open")

    def test_bssid_uppercased(self):
        result = ScannerService._parse_iw_scan(self.IW_OUTPUT)
        self.assertEqual(result[0]["bssid"], "AA:BB:CC:DD:EE:FF")


# ── Synchronous methods ──────────────────────────────────────────────────────

class TestScannerSync(unittest.TestCase):

    def setUp(self):
        self.svc = ScannerService()

    def test_wireless_interfaces_parses_iw_output(self):
        iw_output = (
            "phy#0\n"
            "    Interface wlan0\n"
            "        type managed\n"
            "        addr aa:bb:cc:dd:ee:ff\n"
        )
        with patch.object(_scanner_mod, "_run", return_value=(0, iw_output, "")):
            result = self.svc.wireless_interfaces()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["interface"], "wlan0")

    def test_wireless_interfaces_returns_empty_on_error(self):
        with patch.object(_scanner_mod, "_run", return_value=(1, "", "iw not found")):
            result = self.svc.wireless_interfaces()
        self.assertEqual(result, [])

    def test_ping_valid_host_accepted(self):
        ping_out = "rtt min/avg/max/mdev = 1.0/2.5/4.0/1.0 ms"
        with patch.object(_scanner_mod, "_run", return_value=(0, ping_out, "")):
            result = self.svc.ping("192.168.1.1")
        self.assertTrue(result["ok"])
        self.assertIn("ms", result["latency"])

    def test_ping_invalid_host_rejected(self):
        result = self.svc.ping("host; rm -rf /")
        self.assertFalse(result["ok"])
        self.assertIn("Invalid", result["error"])

    def test_ping_unreachable_host(self):
        with patch.object(_scanner_mod, "_run", return_value=(1, "", "")):
            result = self.svc.ping("10.255.255.1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unreachable")

    def test_dns_lookup_invalid_hostname_rejected(self):
        result = self.svc.dns_lookup("host; cat /etc/passwd")
        self.assertFalse(result["ok"])

    def test_port_scan_invalid_target_rejected(self):
        result = self.svc.start_port_scan("target; evil")
        self.assertFalse(result["ok"])
        self.assertIn("Invalid target", result["error"])

    def test_rfkill_status_parses_output(self):
        rfkill_out = (
            "0: phy0: Wireless LAN\n"
            "    Soft blocked: no\n"
            "    Hard blocked: no\n"
        )
        with patch.object(_scanner_mod, "_run", return_value=(0, rfkill_out, "")):
            result = self.svc.rfkill_status()
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["soft_block"])
        self.assertFalse(result[0]["hard_block"])

    def test_rfkill_hard_blocked_detected(self):
        rfkill_out = (
            "0: hci0: Bluetooth\n"
            "    Soft blocked: no\n"
            "    Hard blocked: yes\n"
        )
        with patch.object(_scanner_mod, "_run", return_value=(0, rfkill_out, "")):
            result = self.svc.rfkill_status()
        self.assertTrue(result[0]["hard_block"])


# ── Async scan lifecycle ──────────────────────────────────────────────────────

class TestScannerAsync(unittest.TestCase):

    def setUp(self):
        self.svc = ScannerService()

    def test_start_network_scan_returns_scanning(self):
        with patch.object(self.svc, "_run_network_scan"):
            result = self.svc.start_network_scan()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "scanning")

    def test_start_network_scan_rejects_duplicate(self):
        with patch.object(self.svc, "_run_network_scan"):
            self.svc.start_network_scan()
            result = self.svc.start_network_scan()
        self.assertFalse(result["ok"])
        self.assertIn("already in progress", result["error"])

    def test_start_bluetooth_scan_returns_scanning(self):
        with patch.object(self.svc, "_run_bluetooth_scan"):
            result = self.svc.start_bluetooth_scan()
        self.assertTrue(result["ok"])

    def test_start_bluetooth_scan_rejects_duplicate(self):
        with patch.object(self.svc, "_run_bluetooth_scan"):
            self.svc.start_bluetooth_scan()
            result = self.svc.start_bluetooth_scan()
        self.assertFalse(result["ok"])

    def test_start_rf_scan_returns_scanning(self):
        with patch.object(self.svc, "_run_rf_scan"):
            result = self.svc.start_rf_scan()
        self.assertTrue(result["ok"])

    def test_start_wifi_scan_rejects_duplicate(self):
        with patch.object(self.svc, "_run_wifi_scan"):
            self.svc.start_wifi_scan()
            result = self.svc.start_wifi_scan()
        self.assertFalse(result["ok"])

    def test_network_result_idle_initially(self):
        self.assertEqual(self.svc.network["status"], "idle")

    def test_bluetooth_result_idle_initially(self):
        self.assertEqual(self.svc.bluetooth["status"], "idle")

    def test_rf_result_idle_initially(self):
        self.assertEqual(self.svc.rf["status"], "idle")


# ── _run helper ───────────────────────────────────────────────────────────────

class TestRunHelper(unittest.TestCase):
    """Tests for the _run() subprocess wrapper in scanner module."""

    def test_returns_stdout_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")
            rc, out, err = _scanner_mod._run(["echo", "hi"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "output")

    def test_returns_minus_one_on_file_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            rc, out, err = _scanner_mod._run(["nonexistent_tool"])
        self.assertEqual(rc, -1)
        self.assertIn("not found", err)

    def test_returns_minus_one_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["cmd"], 1)):
            rc, out, err = _scanner_mod._run(["slow_tool"], timeout=1)
        self.assertEqual(rc, -1)
        self.assertIn("Timeout", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
