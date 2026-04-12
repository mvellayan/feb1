#!/usr/bin/env bash
set -euo pipefail

cd /home/ec2-user

# Update base OS
sudo dnf upgrade -y

# Install GNOME desktop (correct group name for AL2023)
sudo dnf groupinstall "Server with GUI" -y

# Make graphical target the default boot target
sudo systemctl set-default graphical.target

#install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh


# Reboot is required after desktop installation
# Run 03-install-dcv-and-create-session.sh after reconnecting
echo "Rebooting. Reconnect via SSH and run 03-install-dcv-and-create-session.sh"
sudo reboot
