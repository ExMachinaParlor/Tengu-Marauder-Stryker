"""
Hardware bridge for the SunFounder FusionHat+.

Wraps the fusion_hat import so the rest of the application degrades
gracefully when the HAT is not present (dev machines, CI, etc.).
"""

import logging

log = logging.getLogger(__name__)

try:
    from fusion_hat.motor import Motor  # type: ignore
    AVAILABLE = True
    log.info("fusion_hat loaded — hardware is available")
except ImportError:
    Motor = None
    AVAILABLE = False
    log.warning("fusion_hat not installed — hardware features disabled")
except Exception as exc:
    Motor = None
    AVAILABLE = False
    log.warning("fusion_hat import failed (%s: %s) — hardware features disabled",
                type(exc).__name__, exc)
