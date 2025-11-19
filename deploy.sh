#!/bin/bash
#
# Core Cast Server - Docker Deployment Script (Password Version)
#
# This script securely syncs the application source code from the
# local './server' directory to the '~/corecast-server' directory
# on the remote server, then triggers a Docker Compose build and restart.
#
# !! IMPORTANT !!
# This version does NOT use SSH keys and will prompt you
# for your server password multiple times.
#

# --- Configuration ---
# ▼▼▼ EDIT THESE VALUES ▼▼▼

# Set the SSH user and IP address of your remote server
SERVER_USER="evan"
SERVER_IP="192.168.1.213"

# Set the local subdirectory containing your Docker code
# (This script assumes it's run from the project root)
PROJECT_DIR_LOCAL="./"

# Set the directory on the server where the project will be deployed
# (User's home directory, as you requested)
PROJECT_DIR_REMOTE="~/corecast-server"

# --- End Configuration ---

# 'set -e' makes the script exit immediately if any command fails
set -e

# --- Step 1: Sync Files ---
echo "🚀 Syncing Core Cast server files to $SERVER_USER@$SERVER_IP..."
echo "   Local source: $PROJECT_DIR_LOCAL"
echo "   Remote destination: $PROJECT_DIR_REMOTE"
echo "   (You will be prompted for your password for the file sync...)"

# The trailing slash on '$PROJECT_DIR_LOCAL/' is CRITICAL.
# It tells rsync to copy the *contents* of the 'server' directory,
# not the directory itself.
#
# We remove the '-e "ssh -i $SERVER_SSH_KEY"' part to allow
# rsync to use password authentication.
rsync -avzP \
    --delete \
    --exclude=".git/" \
    --exclude="deploy.sh" \
    --exclude="*.pyc" \
    --exclude="__pycache__/" \
    --exclude="ssh_keys" \
    --exclude="test.py" \
    "$PROJECT_DIR_LOCAL" \
    "$SERVER_USER@$SERVER_IP:$PROJECT_DIR_REMOTE/"

if [ $? -ne 0 ]; then
    echo "❌ Rsync failed. Aborting."
    exit 1
fi

echo "✅ File sync complete."


