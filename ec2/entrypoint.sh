#!/bin/bash
set -euo pipefail

IBC_DIR="/opt/ibc"
IBC_INI="/root/ibc/config.ini"
IBG_DIR="/opt/ibgateway"
SETTINGS_DIR="/root/Jts"
LOG_DIR="/root/ibc/logs"

# ── Credentials ───────────────────────────────────────────────────────────────
# Read from environment variables (set in .env via docker-compose).
IB_USER="${TWS_USERID:-}"
IB_PASS="${TWS_PASSWORD:-}"
TRADING_MODE="${TRADING_MODE:-paper}"
IBC_VERSION="${IBC_VERSION:-3.23.0}"

if [[ -z "$IB_USER" || -z "$IB_PASS" ]]; then
    echo "ERROR: TWS_USERID and TWS_PASSWORD must be set (in .env or docker-compose environment)"
    exit 1
fi

# ── Download and extract IBC if not present ──────────────────────────────────
if [[ ! -f "$IBC_DIR/IBCLinux.sh" ]]; then
    echo "[entrypoint] Downloading IBC ${IBC_VERSION}..."
    mkdir -p /tmp/ibc_work
    wget -q "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip" \
        -O /tmp/ibc.zip
    echo "[entrypoint] Extracting IBC..."
    unzip -q /tmp/ibc.zip -d /tmp/ibc_work

    echo "[entrypoint] Contents of /tmp/ibc_work:"
    ls -la /tmp/ibc_work/

    # IBC may extract into a subdirectory
    EXTRACTED=$(find /tmp/ibc_work -maxdepth 1 -type d ! -name . | head -1)
    if [[ -n "$EXTRACTED" && "$EXTRACTED" != "/tmp/ibc_work" ]]; then
        echo "[entrypoint] Moving from $EXTRACTED to $IBC_DIR"
        cp -r "$EXTRACTED"/* "$IBC_DIR/"
    else
        echo "[entrypoint] Moving from /tmp/ibc_work to $IBC_DIR"
        cp -r /tmp/ibc_work/* "$IBC_DIR/"
    fi

    chmod +x "$IBC_DIR"/*.sh 2>/dev/null || true
    chmod +x "$IBC_DIR"/scripts/*.sh 2>/dev/null || true

    echo "[entrypoint] Contents of $IBC_DIR:"
    ls -la "$IBC_DIR/" | head -20

    rm -rf /tmp/ibc.zip /tmp/ibc_work
    echo "[entrypoint] IBC ready at ${IBC_DIR}"
else
    echo "[entrypoint] IBC already present, skipping download"
fi

# ── Generate IBC config ───────────────────────────────────────────────────────
# Written fresh on every start so credentials never persist in the image layer.
cat > "$IBC_INI" <<EOF
LogToConsole=yes
FIX=no

IbLoginId=${IB_USER}
IbPassword=${IB_PASS}
TradingMode=${TRADING_MODE}

# Auto-accept the incoming API connection dialog (no manual click needed)
AcceptIncomingConnectionAction=accept

# If another session is detected, take over (handles restarts after daily shutdown)
ExistingSessionDetectedAction=primaryoverride

ReadOnlyLogin=no
AcceptNonBrokerageAccountWarning=yes
AcceptEulaConfirmation=yes

# No MFA on this account
EOF

echo "[entrypoint] IBC config written (user: ${IB_USER}, mode: ${TRADING_MODE})"

# ── Virtual display ───────────────────────────────────────────────────────────
# IB Gateway requires an X11 display even in headless server use.
echo "[entrypoint] Starting Xvfb virtual display on :1 ..."
Xvfb :1 -screen 0 1280x800x24 -nolisten tcp &

# Wait for display to be ready (up to 10 s)
for i in $(seq 1 10); do
    xdpyinfo -display :1 >/dev/null 2>&1 && break
    sleep 1
done
echo "[entrypoint] Display ready."

# ── Locate IB Gateway binary ──────────────────────────────────────────────────
IBG_BIN="$(find "$IBG_DIR" -maxdepth 4 -type f -name 'ibgateway' | head -n 1)"
if [[ -z "$IBG_BIN" ]]; then
    echo "ERROR: 'ibgateway' binary not found under $IBG_DIR"
    find "$IBG_DIR" -maxdepth 4 -type f | head -20
    exit 1
fi
IBG_BIN_DIR="$(dirname "$IBG_BIN")"
echo "[entrypoint] IB Gateway binary dir: ${IBG_BIN_DIR}"

# ── Launch IB Gateway via IBC ─────────────────────────────────────────────────
# gatewaystart.sh is the IBC launcher for IB Gateway
# It reads config.ini from the current IBC directory
echo "[entrypoint] Launching IB Gateway (IBC will handle login)..."
cp "$IBC_INI" "$IBC_DIR/config.ini"

# Run from IBC directory with proper env
cd "$IBC_DIR"
export TWS_SETTINGS_PATH="$SETTINGS_DIR"

# Launch (gatewaystart.sh exits after launching, so don't background it)
echo "[entrypoint] Running gatewaystart.sh..."
bash ./gatewaystart.sh "$IBG_BIN_DIR" 2>&1 &

# Wait for the actual ibgateway Java process to start
echo "[entrypoint] Waiting for ibgateway process..."
for i in $(seq 1 120); do
    if pgrep -f "java.*ibgateway" >/dev/null 2>&1; then
        echo "[entrypoint] ibgateway Java process is running"
        break
    fi
    sleep 1
    if [[ $((i % 10)) -eq 0 ]]; then
        echo "[entrypoint] Still waiting ($i/120)..."
    fi
done

# Keep container alive by monitoring the Java process
echo "[entrypoint] Monitoring ibgateway..."
while true; do
    if ! pgrep -f "java.*ibgateway" >/dev/null 2>&1; then
        echo "[entrypoint] ibgateway process died, exiting"
        exit 1
    fi
    sleep 10
done
