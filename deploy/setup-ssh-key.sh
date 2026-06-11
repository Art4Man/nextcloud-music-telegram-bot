#!/usr/bin/env bash
# Generate a dedicated ed25519 keypair for the bot and install the public key
# in the destination's authorized_keys (asks for the destination password once).
#
# Usage: deploy/setup-ssh-key.sh [user@]dest-host [keyfile]
#   e.g. deploy/setup-ssh-key.sh root@100.64.0.42
set -euo pipefail

DEST=${1:?usage: setup-ssh-key.sh [user@]dest-host [keyfile]}
KEYFILE=${2:-secrets/nc-music-bot_ed25519}

mkdir -p "$(dirname "$KEYFILE")"
if [[ ! -f "$KEYFILE" ]]; then
    ssh-keygen -t ed25519 -N "" -C "nc-music-bot" -f "$KEYFILE"
    echo "Generated $KEYFILE"
else
    echo "Reusing existing $KEYFILE"
fi

ssh-copy-id -i "${KEYFILE}.pub" "$DEST"

echo
echo "Key installed. In .env set:"
echo "  DEST_SSH_KEY_PATH=$(cd "$(dirname "$KEYFILE")" && pwd)/$(basename "$KEYFILE")"
echo "(With docker compose, ./secrets is mounted at /secrets — use DEST_SSH_KEY_PATH=/secrets/$(basename "$KEYFILE"))"
