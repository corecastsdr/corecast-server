# Dockerfile (Final, Bypassing /etc/ssh completely)

FROM debian:12

# === STEP 1: Install Dependencies ===
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    fail2ban \
    gnuradio \
    soapysdr-tools \
    python3-soapysdr \
    soapysdr-module-rtlsdr \
    soapysdr-module-remote \
    soapysdr-module-osmosdr \
    soapysdr-module-audio \
    soapyremote-server \
    rtl-sdr \
    python3-scipy \
    python3-websockets \
    netcat-traditional \
    proxychains4 \
    python3-httpx \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# === STEP 2: Create User and Generate Host Keys in a SAFE location ===
RUN mkdir -p /run/sshd && \
    mkdir -p /app/host_keys && \
    ssh-keygen -t rsa -b 4096 -f /app/host_keys/ssh_host_rsa_key -N "" && \
    ssh-keygen -t ed25519 -f /app/host_keys/ssh_host_ed25519_key -N ""


# === STEP 3: Copy ALL Application Code and Configs ===
WORKDIR /app
COPY main.py .
COPY entrypoint.sh .
COPY sshd_config .
COPY handle_connection.sh .


RUN chmod +x entrypoint.sh handle_connection.sh

# === STEP 4: Define the Container's Entrypoint and Ports ===
EXPOSE 22
EXPOSE 2222
EXPOSE 50350
EXPOSE 50351
EXPOSE 8001
EXPOSE 8002

ENTRYPOINT ["/app/entrypoint.sh"]
