#!/bin/bash
#
# Core Cast Server - Entrypoint (Dynamic REVERSE SOCKS Proxy Version)
#
set -e

ROLE=${ROLE:-all}
ROLE=$(echo "$ROLE" | tr '[:upper:]' '[:lower:]')
echo "--- Core Cast Entrypoint: Detected ROLE=$ROLE ---"

# --- 1. Start SSH Server ---
echo "✅ Preparing to start SSH server..."
CORECAST_SSH_USER=${CORECAST_SSH_USER:-corecast-host}
if [ -z "$CORECAST_HOST_PASSWORD" ]; then
    echo "❌ FATAL: CORECAST_HOST_PASSWORD environment variable is not set!"
    exit 1
fi

echo "   Creating SSH user '$CORECAST_SSH_USER'..."
# FIX: '2 outlandish' changed to '2>&1'
if ! id -u "$CORECAST_SSH_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$CORECAST_SSH_USER"
fi
echo "$CORECAST_SSH_USER:$CORECAST_HOST_PASSWORD" | chpasswd
echo "   User '$CORECAST_SSH_USER' password set."
echo "   Found custom config: /app/sshd_config"
echo "   Found host keys in: /app/host_keys"

# Start the SSH daemon in the background
echo "🔥 Starting SSH server in background..."
/usr/sbin/sshd -D -e -f /app/sshd_config &

# --- 2. Create Proxy Config ---
echo "   Creating /tmp/proxy.conf for 127.0.0.1:8080"
echo "[ProxyList]" > /tmp/proxy.conf
echo "socks5 127.0.0.1 8080" >> /tmp/proxy.conf

#
# ▼▼▼ THIS IS THE FIX ▼▼▼
#
# We will now wait until the host connects and establishes
# the reverse tunnel on port 8080.
#
echo "⏳ Waiting for host to connect and establish reverse tunnel (127.0.0.1:8080)..."

# We use 'nc -z' to scan for the listening port.
# The loop will continue as long as 'nc' fails (returns non-zero).
while ! nc -z 127.0.0.1 8080; do
    # Safety check: If sshd died, exit the container
    if ! pgrep -x "sshd" > /dev/null; then
      echo "❌ FATAL: sshd process died before a host connected. Check sshd logs."
      exit 1
    fi

    echo "   Tunnel not up yet. Waiting 5 seconds..."
    sleep 5
done

echo "✅ Host connected! Reverse tunnel is active."
#
# ▲▲▲ END OF FIX ▲▲▲
#

# --- 3. Start Main Application ---
# This line is only reached AFTER the tunnel is confirmed to be active.
echo "🚀 Starting Core Cast (ROLE=$ROLE) via dynamic proxy..."
exec proxychains4 -f /tmp/proxy.conf python3 /app/main.py
