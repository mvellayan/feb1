FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:1

# Packages needed for IB Gateway (Java GUI) + headless virtual display
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    xvfb \
    xterm \
    x11-utils \
    libxtst6 \
    libxrender1 \
    libxi6 \
    libxext6 \
    libgl1 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ── IB Gateway ───────────────────────────────────────────────────────────────
# Stable standalone installer bundles its own JRE — no separate Java install needed.
ARG IBGW_URL=https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
RUN wget -q "$IBGW_URL" -O /tmp/ibgw.sh \
    && chmod +x /tmp/ibgw.sh \
    && /tmp/ibgw.sh -q -dir /opt/ibgateway \
    && rm /tmp/ibgw.sh

# ── IBC (Interactive Brokers Controller) ─────────────────────────────────────
# IBC will be downloaded and extracted by the entrypoint on first run
RUN mkdir -p /opt/ibc

# Working directories expected by IBC / IB Gateway
RUN mkdir -p /root/ibc/logs /root/Jts

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# IB Gateway paper-trading API port (default: 4002).
# docker-compose maps host 7497 → container 4002 so arbo701.py needs no changes.
EXPOSE 4002

ENTRYPOINT ["/entrypoint.sh"]
