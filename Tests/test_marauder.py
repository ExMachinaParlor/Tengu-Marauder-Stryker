"""
Unit tests for MarauderService (Control/services/marauder.py).

serial.Serial is mocked for every test so no real device is needed.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

# ── Path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Control"))

# Stub hardware packages that may not exist on the test host.
for _mod in ["fusion_hat", "fusion_hat.motor", "RPi", "RPi.GPIO", "lgpio"]:
    sys.modules.setdefault(_mod, MagicMock())

from services.marauder import MarauderService, ALLOWED_COMMANDS  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_connected(port="/dev/ttyUSB0"):
    """Return a MarauderService with a mocked live serial connection."""
    mock_ser = MagicMock()
    mock_ser.is_open = True
    mock_ser.in_waiting = 0
    with patch("services.marauder.serial.Serial", return_value=mock_ser):
        svc = MarauderService(port=port)
    return svc, mock_ser


def _make_disconnected(port="/dev/ttyUSB99"):
    """Return a MarauderService that failed to connect (port not found)."""
    import serial
    with patch("services.marauder.serial.Serial",
               side_effect=serial.SerialException("No such port")):
        svc = MarauderService(port=port)
    return svc


# ── Connection state ─────────────────────────────────────────────────────────

class TestMarauderConnection(unittest.TestCase):

    def test_connected_true_when_serial_opens(self):
        svc, _ = _make_connected()
        self.assertTrue(svc.connected)

    def test_connected_false_when_serial_fails(self):
        svc = _make_disconnected()
        self.assertFalse(svc.connected)

    def test_port_property_reflects_init_arg(self):
        svc, _ = _make_connected(port="/dev/ttyACM0")
        self.assertEqual(svc.port, "/dev/ttyACM0")

    def test_reconnect_updates_port(self):
        svc, _ = _make_connected()
        mock_ser2 = MagicMock()
        with patch("services.marauder.serial.Serial", return_value=mock_ser2):
            result = svc.reconnect("/dev/ttyACM0")
        self.assertEqual(svc.port, "/dev/ttyACM0")
        self.assertIn("port", result)

    def test_reconnect_closes_previous_connection(self):
        svc, mock_ser = _make_connected()
        with patch("services.marauder.serial.Serial", return_value=MagicMock()):
            svc.reconnect("/dev/ttyACM0")
        mock_ser.close.assert_called_once()

    def test_reconnect_ok_false_when_new_port_fails(self):
        import serial
        svc, _ = _make_connected()
        with patch("services.marauder.serial.Serial",
                   side_effect=serial.SerialException("not found")):
            result = svc.reconnect("/dev/ttyUSB99")
        self.assertFalse(result["ok"])


# ── Command whitelist ─────────────────────────────────────────────────────────

class TestMarauderCommandWhitelist(unittest.TestCase):

    def test_whitelisted_command_accepted(self):
        svc, _ = _make_connected()
        result = svc.send_command("scanap")
        self.assertTrue(result["ok"])

    def test_whitelisted_command_with_args_accepted(self):
        """Base word is what's checked; trailing args are allowed."""
        svc, _ = _make_connected()
        result = svc.send_command("setchannel 6")
        self.assertTrue(result["ok"])

    def test_non_whitelisted_command_rejected(self):
        svc, _ = _make_connected()
        result = svc.send_command("rm -rf /")
        self.assertFalse(result["ok"])
        self.assertIn("not permitted", result["error"])

    def test_empty_command_rejected(self):
        svc, _ = _make_connected()
        result = svc.send_command("   ")
        self.assertFalse(result["ok"])

    def test_command_not_sent_when_not_whitelisted(self):
        svc, mock_ser = _make_connected()
        svc.send_command("malicious_cmd")
        mock_ser.write.assert_not_called()

    def test_whitelist_is_case_insensitive_on_base_word(self):
        """Base word is lowercased before lookup."""
        svc, _ = _make_connected()
        result = svc.send_command("SCANAP")
        self.assertTrue(result["ok"])

    def test_all_whitelisted_commands_known(self):
        """Sanity-check: every entry in ALLOWED_COMMANDS is a non-empty string."""
        for cmd in ALLOWED_COMMANDS:
            self.assertIsInstance(cmd, str)
            self.assertTrue(cmd.strip(), f"Empty string in ALLOWED_COMMANDS: {cmd!r}")


# ── Send behaviour ───────────────────────────────────────────────────────────

class TestMarauderSend(unittest.TestCase):

    def test_send_writes_to_serial_with_crlf(self):
        svc, mock_ser = _make_connected()
        svc.send_command("scanap")
        mock_ser.write.assert_called_once_with(b"scanap\r\n")

    def test_send_appends_to_logs(self):
        svc, _ = _make_connected()
        svc.send_command("scanap")
        self.assertIn("> scanap", svc.logs())

    def test_send_fails_when_not_connected(self):
        svc = _make_disconnected()
        result = svc.send_command("scanap")
        self.assertFalse(result["ok"])
        self.assertIn("not connected", result["error"])

    def test_serial_exception_on_write_returns_error(self):
        svc, mock_ser = _make_connected()
        mock_ser.write.side_effect = OSError("device lost")
        result = svc.send_command("scanap")
        self.assertFalse(result["ok"])

    def test_serial_exception_marks_disconnected(self):
        svc, mock_ser = _make_connected()
        mock_ser.write.side_effect = OSError("device lost")
        svc.send_command("scanap")
        self.assertFalse(svc.connected)


# ── Logs ─────────────────────────────────────────────────────────────────────

class TestMarauderLogs(unittest.TestCase):

    def test_logs_returns_list(self):
        svc, _ = _make_connected()
        self.assertIsInstance(svc.logs(), list)

    def test_logs_empty_on_fresh_connection(self):
        svc, _ = _make_connected()
        self.assertEqual(svc.logs(), [])

    def test_logs_accumulate_sent_commands(self):
        svc, _ = _make_connected()
        svc.send_command("scanap")
        svc.send_command("blescan")
        logs = svc.logs()
        self.assertIn("> scanap", logs)
        self.assertIn("> blescan", logs)

    def test_logs_still_available_when_disconnected(self):
        svc = _make_disconnected()
        self.assertIsInstance(svc.logs(), list)


# ── list_ports ────────────────────────────────────────────────────────────────

class TestMarauderListPorts(unittest.TestCase):

    def test_list_ports_returns_sorted_list(self):
        with patch("services.marauder.glob.glob",
                   side_effect=[["/dev/ttyUSB1", "/dev/ttyUSB0"], []]):
            ports = MarauderService.list_ports()
        self.assertEqual(ports, ["/dev/ttyUSB0", "/dev/ttyUSB1"])

    def test_list_ports_empty_when_no_devices(self):
        with patch("services.marauder.glob.glob", return_value=[]):
            ports = MarauderService.list_ports()
        self.assertEqual(ports, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
