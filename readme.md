# Core Cast Server

Welcome to the open-source repository for the **Core Cast Server**. This is the core backend application that powers a self-hosted Core Cast station, allowing you to share your Software-Defined Radio (SDR) with multiple users over the web.

This server uses GNU Radio to connect to a remote `soapy_server`. It's intelligently designed to connect to an SDR host (like a Raspberry Pi) *even if it's behind a firewall or NAT*. It uses an SSH tunnel to create a SOCKS proxy, which allows the server to securely access the host's SDR. It then processes the wideband I/Q data and serves it to multiple clients simultaneously via WebSockets.

## ⚙️ How It Works: The Architecture

Understanding the architecture is key to a smooth setup. There are two main components: the **SDR Host** and this **Docker Server**.

1.  **The SDR Host (e.g., a Raspberry Pi):**

      * This is the physical computer connected to your SDR hardware (e.g., an RTL-SDR).
      * It runs the standard `soapy_server` application to make the SDR available on a local port (e.g., `localhost:55132`).
      * It runs the `corecast-host` service package. This service reads its configuration from `/etc/corecast/.env`.
      * The service's `start_corecast_host.sh` script establishes an **SSH SOCKS proxy** (`-D` flag) by connecting *from* the host *to* your Docker Server's port `2222`. This makes the host's local network accessible to the server via the proxy.

2.  **The Core Cast Docker Server (This Repo):**

      * This Docker container runs on a public server or VPS.
      * It runs its own SSH server (`sshd`) on port `2222` to *receive* the tunnel connection from your SDR Host.
      * When the host connects, a script (`handle_connection.sh`) inside the container automatically detects the host's IP from the SSH connection.
      * This script then dynamically creates two files:
        1.  `/tmp/proxy.conf`: Tells `proxychains4` how to use the SOCKS proxy now running on the host (e.g., `socks5 $HOST_IP 8080`).
        2.  `/tmp/host_ip.env`: Tells the GNU Radio app to connect to the host's `soapy_server` (e.g., `SDR_REMOTE="$HOST_IP:55132"`).
      * The `entrypoint.sh` script starts `main.py` using `proxychains4`.
      * When `main.py` tries to connect to `$HOST_IP:55132`, `proxychains4` intercepts the connection, sends it *through the SOCKS proxy*, which then forwards it to the `soapy_server` listening on the host's `localhost:55132`.

This project also supports a **Controller/Worker** scaling model (see `docker-compose.scaled.yml`):

  * **`ROLE="controller"`**: A single container that connects to the SDR and publishes the raw I/Q stream on a ZMQ (ZeroMQ) port.
  * **`ROLE="worker"`**: One or more containers that subscribe to the ZMQ stream and handle the CPU-intensive audio demodulation for clients.
  * **`ROLE="all"`**: The default, all-in-one mode perfect for most self-hosters.

## ✅ Prerequisites

  * [Docker](https://www.docker.com/get-started) and [Docker Compose](https://docs.docker.com/compose/install/).
  * An **SDR Host** machine (like a Raspberry Pi) with the `corecast-host` package installed and an SDR (e.g., RTL-SDR) attached.
  * A **Server** (like a VPS or cloud instance) with a public IP address to run this Docker container.
  * Basic knowledge of the command line.

## 🚀 Setup and Installation

Follow these steps to get your server running.

### Step 1: Configure Your SDR Host (e.g., Raspberry Pi)

1.  **Install the `corecast-host` Package:** Follow the instructions for your host's operating system to install the host package. This will set up the `soapyremote-server` and `corecast-host` systemd services.

2.  **Test `soapy_server`:** Manually run `soapy_server --bind="0.0.0.0:55132"` (or your chosen port) and ensure you can connect to it *from another machine on your local network*. This is a critical test. Stop the manual server after testing.

3.  **Configure the Host Service:** Edit the main configuration file with your server's details. This file provides all the necessary environment variables for the service.

    **File:** `/etc/corecast/.env`

    ```bash
    # --- /etc/corecast/.env ---

    # The public IP or domain of your Core Cast Docker Server
    CORECAST_SERVER_IP="your.ip.address"

    # The SSH port from your docker-compose.yml
    CORECAST_SERVER_PORT="2222"

    # The SSH user. DO NOT CHANGE THIS.
    CORECAST_SSH_USER="corecast-host"

    # The port your local soapy_server is running on
    SOAPY_SERVER_PORT="55132"

    # The password for the corecast-host user
    # The default in the Dockerfile is: CorecastPassword123!
    CORECAST_SERVER_PASS="CorecastPassword123!"
    ```

### Step 2: Configure Your Docker Server (VPS)

1.  **Clone This Repository:**

    ```bash
    git clone https://github.com/corecastsdr/corecast-server.git
    cd corecast-server
    ```

2.  **Verify Host Keys (First Time Only):** The `Dockerfile` automatically generates SSH host keys in `/app/host_keys`. If you are providing your own, place them in a `./host_keys` directory on your server *before* building.

3.  **Configure `docker-compose.yml`:** This is the main configuration file. You only need to edit the `environment` section.

    ```yaml
    # docker-compose.yml
    services:
      sdr-control:
        build:
          context: .
          dockerfile: Dockerfile
        image: your-username/corecast-server # Change this!
        container_name: corecast-server
        restart: unless-stopped
        ports:
          # --- Public Ports ---
          - "2222:2222"          # For the SDR Host's SSH tunnel
          - "50351:50351"        # Waterfall WebSocket
          - "50350:50350"        # Audio WebSocket
          - "8002:8002"          # Public metrics (listener count)

        volumes:
          # This maps the provided sshd_config into the container
          - ./sshd_config:/app/sshd_config:ro
          # This maps the generated SSH keys for persistence
          # - ./host_keys:/app/host_keys # Uncomment to use persistent keys

        environment:
          # --- Core Role ---
          ROLE: "all" # Use "all" for a single-server setup

          # --- SDR Configuration ---
          # This is set by the SSH handshake, but good to have a default
          SDR_REMOTE: "127.0.0.1:55132"
          REMOTE_TIMEOUT_MS: "100000"
          CENTER: "104100000"    # 104.1 MHz
          SPAN_RATE: "2048000"   # 2.048 MHz
          FFT_SIZE: "2048"
          GAIN_DB: "20"
    ```

### Step 3: Build and Run the Server

1.  **Build the Docker Image:** This will build your server image.

    ```bash
    sudo docker-compose build
    ```

2.  **Start the Server:** This will start the server in the background.

    ```bash
    sudo docker-compose up -d
    ```

3.  **Check Logs:** Make sure the server started correctly. You should see it waiting for the host to connect.

    ```bash
    sudo docker-compose logs -f
    ```

    You should see: `⏳ Waiting for SDR host to connect and provide IP...`

### Step 4: Start the Host Service

1.  **Go to your SDR Host** (e.g., Raspberry Pi).

2.  **Enable and Start the Service:**

    ```bash
    # Enable the service to start on boot
    sudo systemctl enable corecast-host.service

    # Start the service now
    sudo systemctl start corecast-host.service
    ```

3.  **Check Status:**

      * `systemctl status corecast-host.service` (Should show "active (running)")
      * `journalctl -u corecast-host.service -f` (Should show the SSH connection attempt)

4.  Check your **Docker Server logs** again (`sudo docker-compose logs -f`). You should now see:

    ```
    ✅ SDR Host connected from [HOST_IP].
    🚀 Starting Core Cast (ROLE=all) via dynamic proxy...
    ...
    [probe] opening 127.0.0.1:55132 ...
    [probe] chosen_rate=2048000
    🌈  ws://<host>:50351  (waterfall)
    🔊  ws://<host>:50350  (audio)
    ```

Your server is now live and processing radio data\!

-----

## 💻 Quick Commands (Developer Reference)

A collection of all the essential commands for managing your server.

```bash
# Build the Docker image from scratch
sudo docker-compose build --no-cache

# Build the image using cache (faster)
sudo docker-compose build

# Start the server in the background (detached)
sudo docker-compose up -d

# Stop and remove the server container
sudo docker-compose down

# View the live logs from the server
sudo docker-compose logs -f

# Restart the server
sudo docker-compose restart

# Open a bash shell inside the running container for debugging
sudo docker-compose exec corecast-server /bin/bash

# --- Common Workflow: Rebuild and Restart ---
# (After pulling code changes or editing files)
sudo docker-compose down
sudo docker-compose build
sudo docker-compose up -d

# --- Scaling Commands (Advanced) ---
# Start the controller (using the scaled-out compose file)
sudo docker-compose -f docker-compose.scaled.yml up -d controller

# Scale up to 3 audio workers
sudo docker-compose -f docker-compose.scaled.yml up -d --scale worker=3
```

-----

## 🌐 Connecting to the Core Cast Website

This server is the *backend* piece of the Core Cast platform. By itself, it just serves data. To connect it to the main **Core Cast website** (corecast.io or your own fork), you need to:

1.  **Register Your Station:** On the Core Cast website, you will typically have a "My Stations" area where you can register a new station. This will provide you with a unique **`station_uuid`**.

2.  **Point Your Station to Your Server:** In the station settings on the website, you will need to provide the public IP or domain name of your Docker Server (e.g., `sdr.my-vps.com`).

3.  **Configure API Keys (Optional):** If you are using the official platform or your own fork with metrics, you must set the `METRICS_API_URL` and `INTERNAL_API_KEY` environment variables in your `docker-compose.yml`. This allows your server to report its listener count and other session data back to the main website.

When a user clicks on your station on the website, the web application (front-end) will be instructed to open WebSockets directly to your server's IP on ports `50350` (audio) and `50351` (waterfall), passing along the `station_uuid` and their `user_uuid` to log the session.

## ⚖️ License

Copyright (c) 2025-Present Core Cast.

This project is licensed under the **PolyForm Noncommercial License 1.0.0**.

This means it is free to use, modify, and distribute for **non-commercial purposes only**. You may not use this software for any commercial purposes.

See the `LICENSE.md` file for the full text.
