#!/bin/bash
set -e

echo "✅ Preparing to start SSH server in SUPER DEBUG MODE..."
echo " ownership of /etc/ssh:"
ls -la /etc/ssh/host_keys

echo " permissions of /etc/ssh:"
ls -la /etc/ssh/

echo "🔥 Starting SSH server in foreground with max verbosity..."

# This command runs the server in the foreground (-D) with maximum debugging (-ddd)
# It will print every check it performs and the exact reason if it fails.
exec /usr/sbin/sshd -D -f /app/sshd_config &

echo "✅ SSH service is running."
echo "⏳ Waiting for the remote SOCKS proxy tunnel to be established on port 8080..."

# This loop uses netcat (nc) to check if port 8080 is open on localhost.
# It will wait here forever until the remote SDR machine connects and creates the proxy.
while ! nc -z 127.0.0.1 8080; do
  sleep 1 # wait 1 second before checking again
done

echo "✅ SOCKS proxy tunnel is active! Handing off to main Python application..."

# Now that the tunnel is confirmed to be active, we execute the app through it.
exec proxychains4 -f /app/proxychains.conf python3 /app/main.py
