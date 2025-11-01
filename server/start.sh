#!/bin/bash
# This script prepares the environment and then starts the server.

# --- 1. SSH Key Generation ---
# Check if the keys directory is missing
if [ ! -d "./ssh_keys" ]; then
    echo "Directory ./ssh_keys not found. Generating new SSH host keys..."

    # Create the directory
    mkdir ssh_keys

    # Generate the keys
    ssh-keygen -t rsa -b 4096 -f ssh_keys/ssh_host_rsa_key -N ""
    ssh-keygen -t ed25519 -f ssh_keys/ssh_host_ed25519_key -N ""

    echo "✅ SSH host keys generated."
else
    echo "ℹ️  SSH keys already exist. Skipping generation."
fi

