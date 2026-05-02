"""
Unit tests for get_status() (Control/services/status.py).

psutil is mocked so tests run without real hardware telemetry.
"""

import os
import sys
import re
import unittest
from unittest.mock import MagicMock, patch

# ── Path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Control"))

for _mod in ["fusion_hat", "fusion_hat.motor"]:
    sys.modules.setdefault(_mod, MagicMock())

import psutil  # noqa: E402  (real psutil — it's in requirements.txt)
from services.status import get_status  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_drive(available=True):
    svc = MagicMock()
    svc.available = available
    return svc


def _mock_marauder(connected=True):
    svc = MagicMock()
    svc.connected = connected
    return svc


def _psutil_patches(cpu=15.0, ram_used=400, ram_total=4000, ram_pct=10.0,
                    disk_used=8, disk_total=30):
    """Return a dict of psutil mock values suitable for use with patch()."""
    mem = MagicMock()
    mem.used = ram_used * 1_048_576
    mem.total = ram_total * 1_048_576
    mem.percent = ram_pct

    disk = MagicMock()
    disk.used = disk_used * 1_073_741_824
    disk.total = disk_total * 1_073_741_824

    return {
        "psutil.cpu_percent": cpu,
        "psutil.virtual_memory": mem,
        "psutil.disk_usage": disk,
    }


# ── Key presence ─────────────────────────────────────────────────────────────

class TestStatusKeys(unittest.TestCase):

    EXPECTED_KEYS = {
        "cpu_percent", "ram_used_mb", "ram_total_mb", "ram_percent",
        "disk_used_gb", "disk_total_gb", "uptime", "motors", "marauder",
    }

    def test_all_keys_present(self):
        status = get_status()
        self.assertTrue(self.EXPECTED_KEYS.issubset(status.keys()),
                        f"Missing keys: {self.EXPECTED_KEYS - status.keys()}")

    def test_no_unexpected_none_values(self):
        status = get_status()
        for key, val in status.items():
            self.assertIsNotNone(val, f"Key {key!r} is None")


# ── Module health fields ──────────────────────────────────────────────────────

class TestStatusModuleHealth(unittest.TestCase):

    def test_motors_online_when_drive_available(self):
        status = get_status(drive_service=_mock_drive(available=True))
        self.assertEqual(status["motors"], "online")

    def test_motors_offline_when_drive_unavailable(self):
        status = get_status(drive_service=_mock_drive(available=False))
        self.assertEqual(status["motors"], "offline")

    def test_motors_offline_when_no_drive_service(self):
        status = get_status(drive_service=None)
        self.assertEqual(status["motors"], "offline")

    def test_marauder_connected_when_connected(self):
        status = get_status(marauder_service=_mock_marauder(connected=True))
        self.assertEqual(status["marauder"], "connected")

    def test_marauder_offline_when_not_connected(self):
        status = get_status(marauder_service=_mock_marauder(connected=False))
        self.assertEqual(status["marauder"], "offline")

    def test_marauder_offline_when_no_service(self):
        status = get_status(marauder_service=None)
        self.assertEqual(status["marauder"], "offline")


# ── Uptime format ─────────────────────────────────────────────────────────────

class TestStatusUptime(unittest.TestCase):

    def test_uptime_matches_hhmmss_format(self):
        status = get_status()
        self.assertRegex(status["uptime"], r"^\d{2}:\d{2}:\d{2}$",
                         "uptime must be HH:MM:SS")

    def test_uptime_correct_for_known_boot_time(self):
        import time
        fake_boot = time.time() - (2 * 3600 + 15 * 60 + 30)  # 2h 15m 30s ago
        with patch("services.status._boot_time", fake_boot):
            status = get_status()
        self.assertEqual(status["uptime"], "02:15:30")


# ── Numeric fields ────────────────────────────────────────────────────────────

class TestStatusNumericFields(unittest.TestCase):

    def test_cpu_percent_is_float(self):
        status = get_status()
        self.assertIsInstance(status["cpu_percent"], float)

    def test_cpu_percent_in_valid_range(self):
        status = get_status()
        self.assertGreaterEqual(status["cpu_percent"], 0.0)
        self.assertLessEqual(status["cpu_percent"], 100.0)

    def test_ram_used_not_greater_than_total(self):
        status = get_status()
        self.assertLessEqual(status["ram_used_mb"], status["ram_total_mb"])

    def test_disk_used_not_greater_than_total(self):
        status = get_status()
        self.assertLessEqual(status["disk_used_gb"], status["disk_total_gb"])

    def test_ram_total_positive(self):
        status = get_status()
        self.assertGreater(status["ram_total_mb"], 0)

    def test_disk_total_positive(self):
        status = get_status()
        self.assertGreater(status["disk_total_gb"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
