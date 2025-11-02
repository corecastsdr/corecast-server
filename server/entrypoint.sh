#!/bin/bash
#
# Core Cast Server - Entrypoint (Dynamic Handshake Version)
#
# 1. Performs pre-flight checks for SSH config and keys.
# 2. Starts the custom SSH server in the background.
# 3. Waits for the remote SDR host to connect, which runs the
#    'handle_connection.sh' script and creates a /tmp/host_ip.env file.
# 4. Once this "handshake" file is detected, it loads the dynamic IP.
# 5. It then executes the main Python application *through* the
#    dynamically-created proxy config.
#

# Exit immediately if a command exits with a non-zero status.
set -e

# --- 1. Pre-flight Checks & SSH Server Start ---
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

# --- 2. Wait for Dynamic Handshake ---
echo "⏳ Waiting for SDR host to connect and provide IP..."

# Define the path for the file our 'handle_connection.sh' script will create
HANDSHAKE_FILE="/tmp/host_ip.env"

while [ ! -f "$HANDSHAKE_FILE" ]; do
  # Robustness Check: Ensure sshd is still running.
  # If it crashes for any reason (e.g., bad config), we must exit.
  if ! pgrep -x "sshd" > /dev/null; then
    echo "❌ FATAL: sshd process died before a host connected. Check sshd logs."
    exit 1
  fi
  sleep 1
done

echo "   Handshake file '$HANDSHAKE_FILE' detected."

# --- 3. Load Dynamic Configuration ---
# Load the dynamic IP and SDR_REMOTE variables from the file
source "$HANDSHAKE_FILE"

# Extract the IP for logging
HOST_IP=$(echo "$SDR_REMOTE" | cut -d':' -f1)

if [ -z "$HOST_IP" ]; then
    echo "❌ FATAL: Handshake file was incomplete. Could not read IP."
    exit 1
fi

echo "✅ SDR Host connected from $HOST_IP."

# --- 4. Start Main Application (via Proxy) ---
echo "🚀 Starting Core Cast main.py via dynamic proxy..."

# The 'SDR_REMOTE' env var is now correctly set for the Python app.
# We point proxychains to the dynamic config file created by the handshake.
exec proxychains4 -f /tmp/proxy.conf python3 /app/main.py
