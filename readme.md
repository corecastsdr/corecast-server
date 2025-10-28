
# Core Cast Server

This repository contains the server-side application for Core Cast, a multi-user, web-based Software-Defined Radio (SDR) platform.

This server uses GNU Radio to connect to a remote `soapy_server` (even one behind a NAT/firewall via an SSH reverse tunnel), process the wideband I/Q data, and serve it to multiple clients simultaneously via WebSockets.

## Prerequisites

* [Docker](https://www.docker.com/get-started)
* [Docker Compose](https://docs.docker.com/compose/install/)

## File Structure

Your project directory should look like this:
```
.
├── docker-compose.yml
├── Dockerfile
├── main.py
├── entrypoint.sh
├── sshd\_config
└── (proxychains.conf)  \<-- Optional, if you use it
```


## Configuration

All configuration is managed in the `docker-compose.yml` file under the `environment` section.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SDR_REMOTE` | `127.0.0.1:55132` | The `IP:PORT` of the remote `soapy_server` this app will connect to. |
| `REMOTE_TIMEOUT_MS`| `100000` | SoapySDR remote connection timeout in milliseconds. |
| `CENTER` | `104100000` | Center frequency (in Hz) to tune the SDR to. |
| `SPAN_RATE` | `1024000` | Sample rate (in Hz) to request from the SDR. |
| `FFT_SIZE` | `2048` | The FFT size for the waterfall display. |
| `AUD_RATE` | `48000` | The audio sample rate (in Hz) to send to clients. |
| `GAIN_DB` | `20` | The tuner gain (in dB). |
| `REMOTE_MTU` | `1280` | **(Important)** Sets the network MTU for the SoapyRemote connection. |

## 🐳 All the Commands You'll Ever Need

Here are the essential Docker Compose commands for managing your server.

### Build the Docker Image

Builds the `corecast-server` image based on the `Dockerfile`.

```bash
docker compose build
````

  * **To force a clean rebuild (no cache):**
    ```bash
    docker compose build --no-cache
    ```

### Start the Server

Starts the server in detached mode (runs in the background).

```bash
docker compose up -d
```

  * **To start in the foreground (see logs live):**
    ```bash
    docker compose up
    ```

### Stop the Server

Stops and removes the running container.

```bash
docker compose down
```

### View Server Logs

Follows the logs from the running server. (Press `Ctrl+C` to exit).

```bash
docker compose logs -f
```

### Restart the Server

A quick way to stop and restart the service.

```bash
docker compose restart
```

### Open a Shell Inside the Container

If you need to debug or explore the running container, this command opens a `/bin/bash` shell.

Example:

```bash
docker compose exec corecast-server /bin/bash
```

### Rebuild and Restart

A common workflow after changing code:

```bash
docker compose down
docker compose build
docker compose up -d
```

```
```
