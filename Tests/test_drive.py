"""
Unit tests for DriveService (Control/services/drive.py).

fusion_hat is stubbed before project modules load so the bridge sees
AVAILABLE=True and Motor as a MagicMock we can interrogate per test.
Tests that need the no-hardware path patch AVAILABLE=False directly.
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, call

# ── Path & stub setup ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Control"))

# Stub fusion_hat so robot_hat_bridge can import it on any machine.
sys.modules.setdefault("fusion_hat", MagicMock())
sys.modules.setdefault("fusion_hat.motor", MagicMock())

from services.drive import DriveService  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_service(motor_side_effect=None):
    """Return (svc, mock_right, mock_left) with mocked hardware."""
    mock_right = MagicMock(name="right_motor")
    mock_left = MagicMock(name="left_motor")
    MockMotor = MagicMock(side_effect=motor_side_effect or [mock_right, mock_left])
    with patch("services.drive.AVAILABLE", True), patch("services.drive.Motor", MockMotor):
        svc = DriveService()
    return svc, mock_right, mock_left, MockMotor


# ── No-hardware path ─────────────────────────────────────────────────────────

class TestDriveNoHardware(unittest.TestCase):

    def _svc(self):
        with patch("services.drive.AVAILABLE", False):
            return DriveService()

    def test_available_is_false(self):
        self.assertFalse(self._svc().available)

    def test_move_does_not_raise(self):
        self._svc().move(forward=True)

    def test_move_backward_does_not_raise(self):
        self._svc().move(forward=False)

    def test_turn_does_not_raise(self):
        self._svc().turn(right=True)

    def test_stop_does_not_raise(self):
        self._svc().stop()


# ── Hardware initialisation ──────────────────────────────────────────────────

class TestDriveInit(unittest.TestCase):

    def test_available_true_when_motors_ok(self):
        svc, *_ = _make_service()
        self.assertTrue(svc.available)

    def test_two_motors_created(self):
        _, _, _, MockMotor = _make_service()
        self.assertEqual(MockMotor.call_count, 2)

    def test_right_motor_uses_M0(self):
        """Right motor must be wired to FusionHat+ port M0 (PWM P11/P10)."""
        _, _, _, MockMotor = _make_service()
        first_call_port = MockMotor.call_args_list[0].args[0]
        self.assertEqual(first_call_port, "M0")

    def test_left_motor_uses_M3(self):
        """Left motor must be wired to FusionHat+ port M3 (PWM P4/P5)."""
        _, _, _, MockMotor = _make_service()
        second_call_port = MockMotor.call_args_list[1].args[0]
        self.assertEqual(second_call_port, "M3")

    def test_motors_created_with_freq_100(self):
        _, _, _, MockMotor = _make_service()
        for c in MockMotor.call_args_list:
            self.assertEqual(c.kwargs.get("freq"), 100)

    def test_available_false_on_motor_exception(self):
        with patch("services.drive.AVAILABLE", True), \
             patch("services.drive.Motor", side_effect=RuntimeError("i2c error")):
            svc = DriveService()
        self.assertFalse(svc.available)


# ── Motor command routing ────────────────────────────────────────────────────

class TestDriveCommands(unittest.TestCase):

    def test_move_forward_powers_motors(self):
        svc, right, left, _ = _make_service()
        svc.move(forward=True)
        right.power.assert_called_once_with(50)
        left.power.assert_called_once_with(-50)

    def test_move_backward_inverts_sign(self):
        svc, right, left, _ = _make_service()
        svc.move(forward=False)
        right.power.assert_called_once_with(-50)
        left.power.assert_called_once_with(50)

    def test_turn_right_tank_turn(self):
        svc, right, left, _ = _make_service()
        svc.turn(right=True)
        right.power.assert_called_once_with(-50)
        left.power.assert_called_once_with(-50)

    def test_turn_left_tank_turn(self):
        svc, right, left, _ = _make_service()
        svc.turn(right=False)
        right.power.assert_called_once_with(50)
        left.power.assert_called_once_with(50)

    def test_stop_zeroes_both_motors(self):
        svc, right, left, _ = _make_service()
        svc.stop()
        right.power.assert_called_once_with(0)
        left.power.assert_called_once_with(0)

    def test_left_motor_always_opposite_sign_during_move(self):
        """Physical left-motor inversion: right and left power must have opposite signs."""
        svc, right, left, _ = _make_service()
        svc.move(forward=True)
        r = right.power.call_args[0][0]
        l = left.power.call_args[0][0]
        self.assertEqual(r, -l, "Left motor must receive negated speed vs. right")

    def test_turn_same_sign_exploits_physical_inversion(self):
        """Tank turn: same sign sent to both motors; physical inversion makes them oppose."""
        svc, right, left, _ = _make_service()
        svc.turn(right=True)
        r = right.power.call_args[0][0]
        l = left.power.call_args[0][0]
        self.assertEqual(r, l, "Both motors must receive same sign for tank turn")


# ── Watchdog ─────────────────────────────────────────────────────────────────

class TestDriveWatchdog(unittest.TestCase):

    def test_watchdog_fires_and_stops_motors(self):
        with patch("services.drive.WATCHDOG_TIMEOUT", 0.05):
            svc, right, left, _ = _make_service()
            svc.move(forward=True)
            time.sleep(0.15)  # well past the 50ms watchdog
        right.power.assert_any_call(0)
        left.power.assert_any_call(0)

    def test_watchdog_resets_on_new_command(self):
        """Issuing a new command before timeout cancels the previous watchdog."""
        with patch("services.drive.WATCHDOG_TIMEOUT", 0.08):
            svc, right, left, _ = _make_service()
            svc.move(forward=True)
            time.sleep(0.04)        # halfway through first watchdog
            svc.move(forward=True)  # resets the clock
            time.sleep(0.04)        # still inside new watchdog window

        # power(0) must not have been called; only power(50) and power(-50)
        stop_calls = [c for c in right.power.call_args_list if c == call(0)]
        self.assertEqual(len(stop_calls), 0,
                         "Motors stopped prematurely — watchdog reset did not work")

    def test_stop_cancels_watchdog(self):
        """Explicit stop() should prevent the watchdog from firing a second time."""
        with patch("services.drive.WATCHDOG_TIMEOUT", 0.05):
            svc, right, left, _ = _make_service()
            svc.move(forward=True)
            svc.stop()          # explicit stop cancels watchdog
            right.power.reset_mock()
            left.power.reset_mock()
            time.sleep(0.1)     # past where watchdog would have fired

        # After explicit stop, no further power() calls expected
        right.power.assert_not_called()
        left.power.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
