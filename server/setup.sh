#!/bin/bash
#
# Core Cast Server - First-Time Setup Script
#
# 1. Generates SSH host keys for the server.
# 2. Generates a .env file with a secure, random password for the SDR host.
#

# --- 1. SSH Key Generation ---
if [ ! -d "./ssh_keys" ]; then
    echo "Directory ./ssh_keys not found. Generating new SSH host keys..."
    mkdir -p ssh_keys
    ssh-keygen -t rsa -b 4096 -f ssh_keys/ssh_host_rsa_key -N ""
    ssh-keygen -t ed25519 -f ssh_keys/ssh_host_ed25519_key -N ""
    echo "✅ SSH host keys generated."
else
    echo "ℹ️  SSH keys already exist. Skipping generation."
fi

# --- 2. Environment File Generation ---
ENV_FILE="./.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "$ENV_FILE not found. Generating new environment file..."

    # Generate a 32-character secure random password
    # Uses 'openssl' for broad compatibility
    NEW_PASS=$(openssl rand -base64 24)

    # Create the .env file
    cat > "$ENV_FILE" << EOF
#
# Core Cast Server - Environment Configuration
# This file is loaded by Docker Compose.
#
# --- SSH Host Credentials ---
# This is the user/password the remote SDR host will use to connect.
#
# ❗️ IMPORTANT: Give this password to the SDR host.
#  Will need to be set in: /etc/corecast/.env on your host machine.
#
CORECAST_SSH_USER=corecast-host
CORECAST_HOST_PASSWORD=${NEW_PASS}

EOF

    echo "✅ Generated $ENV_FILE with a new, secure password."
    echo ""
    echo "****************************************************************"
    echo "  REMOTE HOST PASSWORD GENERATED"
    echo "  "
    echo "  The remote SDR host must use this password to connect:"
    echo "  User: corecast-host"
    echo "  Pass: ${NEW_PASS}"
    echo "  "
    echo "  (This is saved in your .env file)"
    echo "****************************************************************"

else
    echo "ℹ️  $ENV_FILE already exists. Skipping generation."
fi

# --- 3. Create .gitignore (if needed) ---
GITIGNORE_FILE="./.gitignore"
if [ ! -f "$GITIGNORE_FILE" ]; then
    echo "Creating .gitignore to protect secrets..."
    cat > "$GITIGNORE_FILE" << EOF
# Local environment variables
.env

# SSH host keys
ssh_keys/

# IDE files
.vscode/
.idea/
EOF
    echo "✅ Created .gitignore."
else
    # Add .env to .gitignore if it's not already there
    if ! grep -q "^\.env$" "$GITIGNORE_FILE"; then
        echo "Adding .env to .gitignore..."
        echo -e "\n# Local environment variables\n.env" >> "$GITIGNORE_FILE"
    fi
    # Add ssh_keys to .gitignore if it's not already there
    if ! grep -q "^ssh_keys/$" "$GITIGNORE_FILE"; then
        echo "Adding ssh_keys/ to .gitignore..."
        echo -e "\n# SSH host keys\nssh_keys/" >> "$GITIGNORE_FILE"
    fi
fi

echo ""
echo "Setup complete. You can now run the server."
