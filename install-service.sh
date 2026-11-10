#!/bin/sh
# Install edrums as a systemd service that starts at boot and restarts
# whenever it exits (e.g. drum kit or sound card unplugged, not yet present).
#
# Usage: ./install-service.sh [ALSA_DEVICE]     e.g. ./install-service.sh plughw:Headset
# Remove: sudo systemctl disable --now edrums && sudo rm /etc/systemd/system/edrums.service
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
USER_NAME=$(id -un)
DEVICE=${1:-}

if [ -n "$DEVICE" ]; then
    AUDIO_ENV="Environment=EDRUMS_AUDIO=$DEVICE"
else
    AUDIO_ENV="# Environment=EDRUMS_AUDIO=plughw:Headset"
fi

sudo tee /etc/systemd/system/edrums.service >/dev/null <<EOF
[Unit]
Description=edrums drum module for the Rock Band drum kit
After=sound.target
# Keep retrying until the drum kit and sound card are there.
StartLimitIntervalSec=0

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
$AUDIO_ENV
ExecStart=/usr/bin/python3 -u $DIR/edrums.py
Restart=always
RestartSec=2
# Prefer the drum module over other processes.
Nice=-10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now edrums.service
echo "edrums service installed and started. Live output: journalctl -u edrums -f"
