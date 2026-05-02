"""
Drive service — motor control with watchdog safety timer.

The watchdog automatically stops motors WATCHDOG_TIMEOUT seconds after
the last command, so a lost browser tab or network drop can't leave the
robot running indefinitely.

Motor wiring note (SunFounder FusionHat+):
  Right motor — FusionHat port M0 (PWM channels P11/P10)
  Left motor  — FusionHat port M3 (PWM channels P4/P5)  (physically inverted)

M0 and M3 are the outermost ports on the FusionHat+ motor header, giving the
cleanest wire routing to wheels on opposite sides of the chassis.

Because the left motor is mounted facing the opposite direction, its
power value must be negated relative to the right motor to achieve
coordinated forward/backward motion. Turn logic uses matching signs so
the physical inversion produces opposite wheel directions (tank turn).

If your motors are plugged into different ports, update MOTOR_RIGHT and
MOTOR_LEFT below to match ("M0"–"M3").
"""

import logging
import threading

from hardware.robot_hat_bridge import AVAILABLE, Motor

log = logging.getLogger(__name__)

WATCHDOG_TIMEOUT = 3.0  # seconds before motors auto-stop
MOTOR_RIGHT = "M0"
MOTOR_LEFT  = "M3"


class DriveService:
    def __init__(self) -> None:
        self._available = False
        self._lock = threading.Lock()
        self._watchdog: threading.Timer | None = None
        self._motor_right = None
        self._motor_left = None

        if AVAILABLE:
            try:
                self._motor_right = Motor(MOTOR_RIGHT, freq=100)
                self._motor_left  = Motor(MOTOR_LEFT,  freq=100)
                self._available = True
                log.info("Drive service online")
            except Exception as exc:
                log.warning("Drive service init failed (%s: %s) — check /dev/i2c-1 and group membership",
                            type(exc).__name__, exc)
        else:
            log.warning("Drive service offline — fusion_hat not available")

    # ── Public interface ────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    def move(self, forward: bool = True) -> None:
        """Drive straight forward or backward."""
        if not self._available:
            return
        speed = 50 if forward else -50
        with self._lock:
            self._motor_right.power(speed)
            self._motor_left.power(-speed)  # left is physically inverted
        self._reset_watchdog()

    def turn(self, right: bool = True) -> None:
        """Tank-turn in place left or right."""
        if not self._available:
            return
        speed = -50 if right else 50
        with self._lock:
            # Same sign → opposite physical directions due to left inversion
            self._motor_right.power(speed)
            self._motor_left.power(speed)
        self._reset_watchdog()

    def stop(self) -> None:
        """Immediately stop both motors and cancel the watchdog."""
        if not self._available:
            return
        with self._lock:
            self._motor_right.power(0)
            self._motor_left.power(0)
        self._cancel_watchdog()
        log.debug("Motors stopped")

    # ── Watchdog ────────────────────────────────────────────────────────────

    def _reset_watchdog(self) -> None:
        self._cancel_watchdog()
        t = threading.Timer(WATCHDOG_TIMEOUT, self._watchdog_fire)
        t.daemon = True
        t.start()
        self._watchdog = t

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _watchdog_fire(self) -> None:
        log.warning("Watchdog timeout — stopping motors")
        self.stop()
