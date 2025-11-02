#!/bin/bash
#
# handle_connection.sh
# This script is run by sshd when the host connects.
# It extracts the host's IP and writes the dynamic config files
# that the main entrypoint is waiting for.

LOG_FILE="/tmp/handle_connection.log"

echo "--- New Connection ---" > "$LOG_FILE"

if [ -z "$SSH_CLIENT" ]; then
  echo "ERROR: SSH_CLIENT variable is not set." >> "$LOG_KEY"
  exit 1
fi

echo "SSH_CLIENT=$SSH_CLIENT" >> "$LOG_FILE"

# 1. Extract IP
HOST_IP=$(echo $SSH_CLIENT | cut -d' ' -f1)

if [ -z "$HOST_IP" ]; then
  echo "ERROR: Could not extract IP from SSH_CLIENT." >> "$LOG_FILE"
  exit 1
fi

echo "Extracted IP: $HOST_IP" >> "$LOG_FILE"

# 2. Create the dynamic proxychains config file
echo "[ProxyList]" > /tmp/proxy.conf
echo "socks5 $HOST_IP 8080" >> /tmp/proxy.conf
echo "Created /tmp/proxy.conf" >> "$LOG_FILE"

# 3. Create the "signal file" that entrypoint.sh is waiting for
echo "export SDR_REMOTE=\"$HOST_IP:55132\"" > /tmp/host_ip.env
echo "Created /tmp/host_ip.env" >> "$LOG_FILE"

# 4. Keep the SSH tunnel alive by sleeping forever
echo "Handshake complete. Sleeping to keep tunnel open..." >> "$LOG_FILE"
sleep infinity
