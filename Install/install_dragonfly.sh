#!/usr/bin/env bash
# Install/install_dragonfly.sh
# Install Vanhoef's Dragonblood / WPA3 SAE research tooling on Tengu Marauder Stryker
# Ref: https://wpa3.mathyvanhoef.com/
#
# Run as root on the Pi:
#   sudo bash Install/install_dragonfly.sh
#
# What this installs:
#   - Build dependencies for patched wpa_supplicant
#   - Vanhoef's dragonblood toolkit (dragonslayer.py, dragondrain.py)
#   - Patched wpa_supplicant with SAE timing logging
#   - hcxtools / hcxdumptool for SAE frame capture
#   - Supporting Python packages

set -euo pipefail

INSTALL_DIR="/opt/dragonblood"
WPA_SUPPLICANT_DIR="/opt/wpa_supplicant_sae"
LOG="/var/log/install_dragonfly.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

log "Starting Dragonblood / WPA3 SAE tool installation"

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
log "Installing system dependencies…"
apt-get update -qq
apt-get install -y --no-install-recommends \
  build-essential git python3-pip python3-dev \
  libssl-dev libnl-3-dev libnl-genl-3-dev \
  pkg-config libdbus-1-dev libpcap-dev \
  hcxdumptool hcxtools \
  aircrack-ng wireless-tools iw \
  2>&1 | tee -a "$LOG"

# ---------------------------------------------------------------------------
# Dragonblood toolkit (dragonslayer, dragondrain)
# ---------------------------------------------------------------------------
log "Cloning Vanhoef dragonblood toolkit → $INSTALL_DIR"
if [[ -d "$INSTALL_DIR" ]]; then
  log "  Already exists — pulling latest"
  git -C "$INSTALL_DIR" pull --ff-only 2>&1 | tee -a "$LOG"
else
  git clone https://github.com/vanhoefm/dragonblood "$INSTALL_DIR" \
    2>&1 | tee -a "$LOG"
fi

# Python dependencies for the toolkit
log "Installing Python dependencies for dragonblood…"
pip3 install --quiet scapy pycryptodome pyOpenSSL 2>&1 | tee -a "$LOG"

# ---------------------------------------------------------------------------
# Patched wpa_supplicant with SAE timing logging
# ---------------------------------------------------------------------------
log "Building patched wpa_supplicant with SAE timing support…"

if [[ -d "$WPA_SUPPLICANT_DIR" ]]; then
  log "  Patched wpa_supplicant already built — skipping"
else
  mkdir -p "$WPA_SUPPLICANT_DIR"

  # Clone hostap (contains wpa_supplicant)
  git clone --depth=1 https://w1.fi/hostap.git "$WPA_SUPPLICANT_DIR/hostap" \
    2>&1 | tee -a "$LOG"

  # Apply Vanhoef's SAE timing patch if available in dragonblood toolkit
  PATCH="$INSTALL_DIR/patches/sae_timing.patch"
  if [[ -f "$PATCH" ]]; then
    log "  Applying SAE timing patch…"
    git -C "$WPA_SUPPLICANT_DIR/hostap" apply "$PATCH" \
      2>&1 | tee -a "$LOG"
  else
    log "  SAE timing patch not found in toolkit — building stock wpa_supplicant"
    log "  Manual patch may be required. See: https://wpa3.mathyvanhoef.com/"
  fi

  # Build configuration
  cat > "$WPA_SUPPLICANT_DIR/hostap/wpa_supplicant/.config" <<'EOF'
CONFIG_SAE=y
CONFIG_SAE_PK=y
CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_IEEE80211W=y
CONFIG_OCV=y
CONFIG_DEBUG_FILE=y
CONFIG_BACKEND=file
EOF

  log "  Compiling wpa_supplicant (this may take a few minutes)…"
  make -C "$WPA_SUPPLICANT_DIR/hostap/wpa_supplicant" -j"$(nproc)" \
    2>&1 | tee -a "$LOG"

  # Install alongside system wpa_supplicant with a distinct name
  install -m 755 \
    "$WPA_SUPPLICANT_DIR/hostap/wpa_supplicant/wpa_supplicant" \
    /usr/local/bin/wpa_supplicant_sae

  log "  Patched wpa_supplicant installed → /usr/local/bin/wpa_supplicant_sae"
fi

# ---------------------------------------------------------------------------
# Helper script — run dragonslayer against a target
# ---------------------------------------------------------------------------
cat > /usr/local/bin/dragonslayer <<EOF
#!/usr/bin/env bash
# Wrapper: run Vanhoef's dragonslayer.py
exec python3 $INSTALL_DIR/dragonslayer.py "\$@"
EOF
chmod +x /usr/local/bin/dragonslayer

cat > /usr/local/bin/dragondrain <<EOF
#!/usr/bin/env bash
# Wrapper: run Vanhoef's dragondrain.py (SAE DoS measurement)
exec python3 $INSTALL_DIR/dragondrain.py "\$@"
EOF
chmod +x /usr/local/bin/dragondrain

# ---------------------------------------------------------------------------
# hcxdumptool SAE capture wrapper
# ---------------------------------------------------------------------------
cat > /usr/local/bin/sae-capture <<'EOF'
#!/usr/bin/env bash
# Capture WPA3 SAE frames with hcxdumptool
# Usage: sae-capture <interface> <output.pcapng> [duration_seconds]
IFACE="${1:?Usage: sae-capture <interface> <output.pcapng> [duration]}"
OUTPUT="${2:?Usage: sae-capture <interface> <output.pcapng> [duration]}"
DURATION="${3:-60}"

echo "[*] Capturing WPA3 SAE frames on $IFACE for ${DURATION}s → $OUTPUT"
timeout "$DURATION" hcxdumptool \
  -i "$IFACE" \
  -o "$OUTPUT" \
  --enable_status=3 \
  --active_beacon \
  || true
echo "[*] Capture complete: $OUTPUT"
echo "[*] Parse with: hcxpcapngtool --json $OUTPUT"
EOF
chmod +x /usr/local/bin/sae-capture

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Dragonblood / WPA3 SAE tools installed"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log " Toolkit        : $INSTALL_DIR"
log " wpa_supplicant : /usr/local/bin/wpa_supplicant_sae"
log " dragonslayer   : /usr/local/bin/dragonslayer"
log " dragondrain    : /usr/local/bin/dragondrain"
log " sae-capture    : /usr/local/bin/sae-capture"
log ""
log " Quick start:"
log "   sae-capture wlan1 capture.pcapng 60"
log "   dragonslayer --interface wlan1 --bssid AA:BB:CC:DD:EE:FF"
log ""
log " C2 integration — assign role from operator console:"
log "   assign <session_id> dragonfly {\"interface\":\"wlan1\",\"target_bssid\":\"AA:BB:CC:DD:EE:FF\"}"
log "   data sae_timing"
log ""
log " Reference: https://wpa3.mathyvanhoef.com/"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
