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
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# === STEP 2: Create User and Generate Host Keys in a SAFE location ===
# We are no longer using /etc/ssh for anything.
# 1. Create a safe directory for our keys inside /app.
# 2. Generate the two most common key types directly into that safe directory.
RUN useradd -m -s /bin/bash corecast-host && \
    echo "corecast-host:CorecastPassword123!" | chpasswd && \
    mkdir -p /run/sshd && \
    mkdir -p /app/host_keys && \
    ssh-keygen -t rsa -b 4096 -f /app/host_keys/ssh_host_rsa_key -N "" && \
    ssh-keygen -t ed25519 -f /app/host_keys/ssh_host_ed25519_key -N ""

# === STEP 3: Copy ALL Application Code and Configs ===
WORKDIR /app
COPY main.py .
COPY entrypoint.sh .
COPY sshd_config .
RUN chmod +x entrypoint.sh

# === STEP 4: Define the Container's Entrypoint and Ports ===
EXPOSE 22
ENTRYPOINT ["/app/entrypoint.sh"]
