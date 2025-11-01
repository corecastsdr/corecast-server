#!/bin/bash
#
# Core Cast Server - Entrypoint (SOCKS Proxy Version)
#
# 1. Starts the custom SSH server in the background.
# 2. Waits for the remote SDR host to connect and establish its
#    DYNAMIC SOCKS proxy tunnel on port 8080.
# 3. Once the proxy is detected, it executes the main Python
#    application *through* proxychains.
#

# Exit immediately if a command exits with a non-zero status.
set -e

# --- 1. Start SSH Server ---
echo "✅ Preparing to start SSH server..."

if [ ! -f /app/sshd_config ]; then
    echo "❌ FATAL: /app/sshd_config not found!"
    exit 1
fi
if [ ! -f /app/host_keys/ssh_host_rsa_key ]; then
    echo "❌ FATAL: /app/host_keys/ssh_host_rsa_key not found!"
    exit 1
fi

echo "   Found custom config: /app/sshd_config"
echo "   Found host keys in: /app/host_keys"
ls -la /app/host_keys

# Start the SSH daemon in the background
echo "🔥 Starting SSH server in background..."
/usr/sbin/sshd -D -e -f /app/sshd_config &

echo "✅ SSH server started. Awaiting connection from SDR host..."

# --- 2. Wait for SOCKS Proxy Tunnel ---

echo "⏳ Waiting for the remote SOCKS proxy tunnel to be established on port 8080..."
echo "   (This requires the remote SDR host to connect with 'ssh -D 8080')"

#
# ▼▼▼ THIS IS THE FIX ▼▼▼
#
# We check the SDR host's IP (192.168.1.115), NOT 'host.docker.internal'.
while ! nc -z -w 2 192.168.1.115 8080; do
  echo "   ... proxy not yet detected at 192.168.1.115:8080. Retrying in 2s..."
  sleep 2 # wait 2 seconds before checking again
done
#
# ▲▲▲ END OF FIX ▲▲▲
#

echo "✅ SOCKS proxy tunnel is active! Handing off to main Python application..."

# --- 3. Start Main Application (via Proxy) ---
#
# Now that the tunnel is confirmed, we execute the app through proxychains.
#
echo "🚀 Starting Core Cast main.py via proxychains..."
exec proxychains4 -f /app/proxychains.conf python3 /app/main.py
