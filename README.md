# Tengu Marauder Stryker (TMS)

Tengu Marauder Mark 3 — offensive multi-purpose mobile robotics platform.

## Hardware

| Component | Role |
|---|---|
| Raspberry Pi | Host compute |
| Robot HAT (PCA9685) | Motor control via I2C |
| ESP32 Marauder | Active wireless attacks |
| Flipper Zero / M5Stick Bruce | RF/IR/NFC |
| HackRF One | SDR |
| USB webcam | Operator video feed |

## Software Stack

- **Runtime:** Docker / Docker Compose (host network mode)
- **Backend:** Python services (Flask + WebSocket)
- **Operator UI:** Browser-based at `http://<pi-ip>:5000`
- **Services:** `drive`, `marauder`, `scanner`, `status`, `speak`

## Quick Start

```bash
# Start (detached)
./tms-start.sh

# Start and follow logs
./tms-start.sh --logs

# Rebuild image then start
./tms-start.sh --rebuild

# Stop
./tms-start.sh --stop
```

`tms-start.sh` detects which hardware devices are present before starting the container, so it is safe to run without all hardware connected.

Running `docker compose up` directly also works but maps no hardware devices.

## Installation

Scripts in [Install/](Install/) set up the Pi host environment:

| Script | Purpose |
|---|---|
| `robot_hat_install.sh` | Robot HAT / motor control drivers |
| `install_device_integrations.sh` | ESP32, Flipper Zero, serial device support |
| `install_active_wireless.sh` | Active wireless tooling |
| `install_passive_recon.sh` | Passive recon tooling |
| `install_host_permissions.sh` | Group/udev permissions for hardware access |

## VPN

See [VPN/setup_tmv_wifi_vpn.sh](VPN/setup_tmv_wifi_vpn.sh) for WireGuard/VPN setup on the Pi's wireless interface.

## Documentation

- [Tengu Marauder Stryker — Black Hat USA 2025](Documentation/Tengu%20Marauder%20Stryker%20Blackhat%20USA.md)
- [Women in Cybersecurity 2026](Documentation/Women%20in%20Cybersecurity%202026.md)

## Repository Structure

```
Tengu-Marauder-Stryker/
├── Control/            # Flask app, operator UI, service modules
│   ├── services/       # drive, marauder, scanner, status, speak
│   ├── static/         # Frontend assets
│   └── templates/      # Jinja2 HTML templates
├── Addons/             # Optional add-on modules
├── Documentation/      # Presentations and write-ups
├── Images/             # Build/reference images
├── Install/            # Host setup scripts
├── Tests/              # Test suite
├── VPN/                # VPN configuration
├── compose.yaml        # Docker Compose definition
├── Dockerfile          # Container build
├── requirements.txt    # Python dependencies
└── tms-start.sh        # Hardware-aware startup script
```
