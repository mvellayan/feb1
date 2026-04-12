#!/usr/bin/env bash
set -euo pipefail

cd /home/ec2-user

# Wait for graphical target to be active before proceeding
echo "Waiting for graphical target..."
timeout 120 bash -c 'until systemctl is-active graphical.target &>/dev/null; do sleep 5; done'

# Import Amazon DCV signing key
sudo rpm --import https://d1uj6qtbmh3dt5.cloudfront.net/NICE-GPG-KEY

# Download the latest AL2023 x86_64 DCV bundle
wget https://d1uj6qtbmh3dt5.cloudfront.net/nice-dcv-amzn2023-x86_64.tgz

# Extract it
tar -xvzf nice-dcv-amzn2023-x86_64.tgz

# Enter extracted directory (dynamic name)
DCV_DIR=$(find . -maxdepth 1 -type d -name 'nice-dcv-*amzn2023*x86_64' | head -n 1)
cd "$DCV_DIR"

# Install DCV server + browser web viewer + virtual session support
sudo dnf install -y \
  ./nice-dcv-server-*.amzn2023.x86_64.rpm \
  ./nice-dcv-web-viewer-*.amzn2023.x86_64.rpm \
  ./nice-xdcv-*.amzn2023.x86_64.rpm

# Start and enable DCV server
sudo systemctl enable dcvserver
sudo systemctl start dcvserver

# Create a virtual session for ec2-user
sudo dcv create-session \
  --owner ec2-user \
  --user ec2-user \
  my-session

echo
echo "DCV session created."
dcv list-sessions
echo "Connect via browser to: https://<PUBLIC_DNS>:8443"
echo "Session ID: my-session"
