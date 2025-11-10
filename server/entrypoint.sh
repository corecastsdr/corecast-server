#!/bin/bash
#
# Core Cast Server - Entrypoint (Upgraded for Controller/Worker Roles)
#
# Reads the `ROLE` env var:
# 1. "controller" or "all": Performs the full SSH handshake, waits for the
#    host, and then runs main.py (via proxychains).
# 2. "worker": Skips the SSH handshake entirely and runs main.py directly,
#    expecting a `ZMQ_HOST_ADDR` to be set.
#

# Exit immediately if a command exits with a non-zero status.
set -e

# --- 0. Determine Role ---
# Default to "all" for backward-compatibility with your old docker-compose.yml
ROLE=${ROLE:-all}
ROLE=$(echo "$ROLE" | tr '[:upper:]' '[:lower:]')
echo "--- Core Cast Entrypoint: Detected ROLE=$ROLE ---"


# --- 1. Handshake Logic (for Controller or All) ---
if [[ "$ROLE" == "controller" ]] || [[ "$ROLE" == "all" ]]; then
  echo "✅ Role requires SSH handshake. Preparing to start SSH server..."

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
  HANDSHAKE_FILE="/tmp/host_ip.env"

  while [ ! -f "$HANDSHAKE_FILE" ]; do
    if ! pgrep -x "sshd" > /dev/null; then
      echo "❌ FATAL: sshd process died before a host connected. Check sshd logs."
      exit 1
    fi
    sleep 1
  done

  echo "   Handshake file '$HANDSHAKE_FILE' detected."

  # --- 3. Load Dynamic Configuration ---
  source "$HANDSHAKE_FILE"
  HOST_IP=$(echo "$SDR_REMOTE" | cut -d':' -f1)

  if [ -z "$HOST_IP" ]; then
      echo "❌ FATAL: Handshake file was incomplete. Could not read IP."
      exit 1
  fi
  echo "✅ SDR Host connected from $HOST_IP."
fi


# --- 4. Start Main Application (Based on Role) ---
if [[ "$ROLE" == "controller" ]] || [[ "$ROLE" == "all" ]]; then
  echo "🚀 Starting Core Cast (ROLE=$ROLE) via dynamic proxy..."
  # The 'SDR_REMOTE' env var is set from the handshake file.
  exec proxychains4 -f /tmp/proxy.conf python3 /app/main.py

elif [[ "$ROLE" == "worker" ]]; then
  echo "🚀 Starting Core Cast (ROLE=$ROLE)..."
  if [ -z "$ZMQ_HOST_ADDR" ]; then
    echo "❌ FATAL: ROLE=worker but ZMQ_HOST_ADDR environment variable is not set!"
    exit 1
  fi
  # No proxy needed, we're just a worker.
  exec python3 /app/main.py

else
  echo "❌ FATAL: Unknown ROLE='$ROLE'. Must be 'controller', 'worker', or 'all'."
  exit 1
fi
