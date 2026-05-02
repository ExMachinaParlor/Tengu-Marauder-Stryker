"""
Integration tests for the Flask HTTP API (Control/operatorcontrol.py).

Services are replaced with MagicMocks after import so no hardware is
needed. The Flask test client is used for all HTTP interactions.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── Path & stub setup ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Control"))

# Stub external packages that may not exist on the test host.
for _mod in ["cv2", "fusion_hat", "fusion_hat.motor", "RPi", "RPi.GPIO", "lgpio"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub serial so MarauderService.__init__ doesn't open a real port.
import unittest.mock as _mock
sys.modules.setdefault("serial", _mock.MagicMock())
sys.modules.setdefault("serial.tools", _mock.MagicMock())
sys.modules.setdefault("serial.tools.list_ports", _mock.MagicMock())

import serial as _serial_stub
_serial_stub.SerialException = Exception  # make it raiseable

# ── App import ───────────────────────────────────────────────────────────────
import operatorcontrol as app_module  # noqa: E402

_app = app_module.app
_app.config["TESTING"] = True


def _client():
    return _app.test_client()


def _inject_mocks(**kwargs):
    """Replace app-level service singletons with mocks for a test."""
    for attr, mock in kwargs.items():
        setattr(app_module, attr, mock)


def _restore_services():
    """Re-inject the real (offline) singletons after each test."""
    pass  # singletons are re-set per test; nothing to restore globally.


# ── UI route ─────────────────────────────────────────────────────────────────

class TestUIRoute(unittest.TestCase):

    def test_index_returns_200(self):
        with _client() as c:
            resp = c.get("/")
        self.assertEqual(resp.status_code, 200)


# ── Drive API ─────────────────────────────────────────────────────────────────

class TestDriveAPI(unittest.TestCase):

    def setUp(self):
        self.mock_drive = MagicMock()
        _inject_mocks(drive=self.mock_drive)

    def test_move_forward(self):
        with _client() as c:
            resp = c.post("/api/move", json={"direction": "forward"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.mock_drive.move.assert_called_once_with(forward=True)

    def test_move_backward(self):
        with _client() as c:
            resp = c.post("/api/move", json={"direction": "backward"})
        self.mock_drive.move.assert_called_once_with(forward=False)

    def test_move_right(self):
        with _client() as c:
            c.post("/api/move", json={"direction": "right"})
        self.mock_drive.turn.assert_called_once_with(right=True)

    def test_move_left(self):
        with _client() as c:
            c.post("/api/move", json={"direction": "left"})
        self.mock_drive.turn.assert_called_once_with(right=False)

    def test_move_stop_via_direction(self):
        with _client() as c:
            c.post("/api/move", json={"direction": "stop"})
        self.mock_drive.stop.assert_called_once()

    def test_move_unknown_direction_returns_400(self):
        with _client() as c:
            resp = c.post("/api/move", json={"direction": "diagonal"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["ok"])

    def test_move_missing_direction_returns_400(self):
        with _client() as c:
            resp = c.post("/api/move", json={})
        self.assertEqual(resp.status_code, 400)

    def test_move_no_body_returns_400(self):
        with _client() as c:
            resp = c.post("/api/move")
        self.assertEqual(resp.status_code, 400)

    def test_stop_endpoint(self):
        with _client() as c:
            resp = c.post("/api/stop")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        self.mock_drive.stop.assert_called_once()


# ── Marauder API ──────────────────────────────────────────────────────────────

class TestMarauderAPI(unittest.TestCase):

    def setUp(self):
        self.mock_marauder = MagicMock()
        self.mock_marauder.send_command.return_value = {"ok": True, "command": "scanap"}
        self.mock_marauder.logs.return_value = ["> scanap"]
        self.mock_marauder.list_ports.return_value = ["/dev/ttyUSB0"]
        self.mock_marauder.port = "/dev/ttyUSB0"
        _inject_mocks(marauder=self.mock_marauder)

    def test_send_command(self):
        with _client() as c:
            resp = c.post("/api/marauder", json={"command": "scanap"})
        self.assertEqual(resp.status_code, 200)
        self.mock_marauder.send_command.assert_called_once_with("scanap")

    def test_send_command_no_body_returns_400(self):
        with _client() as c:
            resp = c.post("/api/marauder", json={})
        self.assertEqual(resp.status_code, 400)

    def test_send_command_empty_string_returns_400(self):
        with _client() as c:
            resp = c.post("/api/marauder", json={"command": ""})
        self.assertEqual(resp.status_code, 400)

    def test_logs_endpoint(self):
        with _client() as c:
            resp = c.get("/api/marauder/logs")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("logs", data)
        self.assertIn("> scanap", data["logs"])

    def test_ports_endpoint(self):
        with _client() as c:
            resp = c.get("/api/marauder/ports")
        data = resp.get_json()
        self.assertIn("ports", data)
        self.assertIn("active", data)

    def test_switch_port(self):
        self.mock_marauder.reconnect.return_value = {"ok": True, "port": "/dev/ttyACM0"}
        with _client() as c:
            resp = c.post("/api/marauder/port", json={"port": "/dev/ttyACM0"})
        self.assertEqual(resp.status_code, 200)
        self.mock_marauder.reconnect.assert_called_once_with("/dev/ttyACM0")

    def test_switch_port_no_port_returns_400(self):
        with _client() as c:
            resp = c.post("/api/marauder/port", json={})
        self.assertEqual(resp.status_code, 400)


# ── Status API ────────────────────────────────────────────────────────────────

class TestStatusAPI(unittest.TestCase):

    REQUIRED_KEYS = {
        "cpu_percent", "ram_used_mb", "ram_total_mb", "uptime",
        "motors", "marauder", "gps",
    }

    def setUp(self):
        mock_scanner = MagicMock()
        mock_scanner.gps_fix.return_value = None
        _inject_mocks(scanner=mock_scanner)

    def test_status_returns_200(self):
        with _client() as c:
            resp = c.get("/api/status")
        self.assertEqual(resp.status_code, 200)

    def test_status_has_required_fields(self):
        with _client() as c:
            data = c.get("/api/status").get_json()
        self.assertTrue(self.REQUIRED_KEYS.issubset(data.keys()),
                        f"Missing: {self.REQUIRED_KEYS - data.keys()}")

    def test_status_gps_offline_when_no_fix(self):
        with _client() as c:
            data = c.get("/api/status").get_json()
        self.assertEqual(data["gps"], "offline")

    def test_status_gps_shows_coords_when_fix(self):
        app_module.scanner.gps_fix.return_value = {"lat": 40.7128, "lon": -74.006}
        with _client() as c:
            data = c.get("/api/status").get_json()
        self.assertIn("40.71280", data["gps"])


# ── Recon / Scan API ──────────────────────────────────────────────────────────

class TestReconAPI(unittest.TestCase):

    def setUp(self):
        self.mock_scanner = MagicMock()
        self.mock_scanner.start_network_scan.return_value = {"ok": True, "status": "scanning"}
        self.mock_scanner.start_bluetooth_scan.return_value = {"ok": True, "status": "scanning"}
        self.mock_scanner.start_rf_scan.return_value = {"ok": True, "status": "scanning"}
        self.mock_scanner.network = {"status": "idle", "data": [], "error": "", "last_run": None}
        self.mock_scanner.bluetooth = {"status": "idle", "data": [], "error": "", "last_run": None}
        self.mock_scanner.rf = {"status": "idle", "data": [], "error": "", "last_run": None}
        self.mock_scanner.wifi = {"status": "idle", "data": [], "error": "", "last_run": None}
        self.mock_scanner.portscan = {"status": "idle", "data": [], "error": "", "last_run": None}
        self.mock_scanner.gps_fix.return_value = None
        _inject_mocks(scanner=self.mock_scanner)

    def test_start_network_scan(self):
        with _client() as c:
            resp = c.post("/api/scan/network", json={})
        self.assertEqual(resp.status_code, 200)
        self.mock_scanner.start_network_scan.assert_called_once()

    def test_poll_network_scan_results(self):
        with _client() as c:
            resp = c.get("/api/scan/network")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status", resp.get_json())

    def test_start_bluetooth_scan(self):
        with _client() as c:
            resp = c.post("/api/scan/bluetooth", json={})
        self.mock_scanner.start_bluetooth_scan.assert_called_once()

    def test_start_rf_scan(self):
        with _client() as c:
            resp = c.post("/api/scan/rf")
        self.mock_scanner.start_rf_scan.assert_called_once()

    def test_ping_no_host_returns_400(self):
        with _client() as c:
            resp = c.post("/api/ping", json={})
        self.assertEqual(resp.status_code, 400)

    def test_ping_with_host(self):
        self.mock_scanner.ping.return_value = {"ok": True, "host": "8.8.8.8", "latency": "2.5 ms"}
        with _client() as c:
            resp = c.post("/api/ping", json={"host": "8.8.8.8"})
        self.assertEqual(resp.status_code, 200)
        self.mock_scanner.ping.assert_called_once_with("8.8.8.8")

    def test_dns_no_host_returns_400(self):
        with _client() as c:
            resp = c.post("/api/dns", json={})
        self.assertEqual(resp.status_code, 400)

    def test_portscan_no_target_returns_400(self):
        with _client() as c:
            resp = c.post("/api/scan/portscan", json={})
        self.assertEqual(resp.status_code, 400)

    def test_portscan_with_target(self):
        self.mock_scanner.start_port_scan.return_value = {"ok": True, "status": "scanning"}
        with _client() as c:
            resp = c.post("/api/scan/portscan", json={"target": "192.168.1.1"})
        self.assertEqual(resp.status_code, 200)
        self.mock_scanner.start_port_scan.assert_called_once_with(
            target="192.168.1.1", flags=""
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
