#!/bin/bash
#
# Core Cast Server - Entrypoint (Simplified Proxy Version)
#
set -e

# --- 1. Start SSH Server ---
# We still run sshd so the host can connect and create the proxy tunnel.
# But we DO NOT wait for it.
echo "--- Core Cast Entrypoint: Detected ROLE=all ---"
echo "✅ Starting SSH server in background..."

# Create the SSH User
CORECAST_SSH_USER=${CORECAST_SSH_USER:-corecast-host}
if [ -z "$CORECAST_HOST_PASSWORD" ]; then
    echo "❌ FATAL: CORECAST_HOST_PASSWORD environment variable is not set!"
    exit 1
fi
echo "   Creating SSH user '$CORECAST_SSH_USER'..."
if ! id -u "$CORECAST_SSH_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$CORECAST_SSH_USER"
fi
echo "$CORECAST_SSH_USER:$CORECAST_HOST_PASSWORD" | chpasswd
echo "   User '$CORECAST_SSH_USER' password set."
echo "   Found custom config: /app/sshd_config"
echo "   Found host keys in: /app/host_keys"
ls -la /app/host_keys
/usr/sbin/sshd -D -e -f /app/sshd_config &
echo "🔥 SSH server is running in background."

# --- 2. Create Proxy Config ---
# We create this file immediately. We don't need the host's IP.
# We will tell proxychains to use the SOCKS proxy on localhost:8080,
# which the host's -D flag will create.
echo "   Creating /tmp/proxy.conf for 127.0.0.1:8080"
echo "[ProxyList]" > /tmp/proxy.conf
echo "socks5 127.0.0.1 8080" >> /tmp/proxy.conf

# --- 3. Start Main Application ---
# We no longer wait. We just start main.py.
# The host script must be running *before* this, but
# if it's not, proxychains will just fail and the container will restart.
echo "🚀 Starting Core Cast (ROLE=all) via dynamic proxy..."
exec proxychains4 -f /tmp/proxy.conf python3 /app/main.py
