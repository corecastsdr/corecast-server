#!/bin/bash
#
# Core Cast Server - Entrypoint
#
# This script performs the following critical boot sequence:
# 1. Starts the custom SSH server in the background.
# 2. Waits for the remote SDR host to connect and establish its
#    reverse SSH tunnel.
# 3. Once the tunnel is detected (by polling the port), it
#    executes the main Python application.
#

# Exit immediately if a command exits with a non-zero status.
set -e

# --- 1. Start SSH Server ---
echo "✅ Preparing to start SSH server..."

# Verify our custom config and keys exist
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
echo "   DEBUG: Listing host keys:"
ls -la /app/host_keys

# Start the SSH daemon in the background
echo "🔥 Starting SSH server in background..."
/usr/sbin/sshd -D -e -f /app/sshd_config &

echo "✅ SSH server started. Awaiting connection from SDR host..."

# --- 2. Wait for Reverse Tunnel ---

# Get the host and port from SDR_REMOTE (e.g., "127.0.0.1:55132")
if [ -z "$SDR_REMOTE" ]; then
    echo "❌ FATAL: SDR_REMOTE environment variable is not set!"
    exit 1
fi

# Parse the variable to get the host and port
SDR_HOST=${SDR_REMOTE%:*}
SDR_PORT=${SDR_REMOTE##*:}

echo "⏳ Waiting for reverse tunnel to be established on $SDR_HOST:$SDR_PORT..."
echo "   (This requires the remote SDR host to connect to this server via SSH)"

# Loop using netcat (nc) to check if the port is open.
# The 'netcat-traditional' package (from Dockerfile) supports '-z'.
while ! nc -z "$SDR_HOST" "$SDR_PORT"; do
  sleep 1 # wait 1 second before checking again
done

echo "✅ Reverse tunnel is active! Handing off to main Python application..."

# --- 3. Start Main Application ---
#
# Now that the tunnel is confirmed, we execute the app.
# We use 'exec' to replace this script with the python process,
# ensuring it becomes PID 1 and receives signals correctly.
#
echo "🚀 Starting Core Cast main.py..."
exec python3 /app/main.py
