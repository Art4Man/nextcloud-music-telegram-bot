#!/usr/bin/env bash
# Provision nc-music-bot on a generic Linux VM WITHOUT Docker (Debian/Ubuntu-flavored):
# dedicated user, /opt/nc-music-bot install, systemd unit.
#
# Run as root from a checkout of the repo:   sudo deploy/install-systemd.sh
set -euo pipefail

APP_DIR=/opt/nc-music-bot
SERVICE_USER=ncmusicbot
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo deploy/install-systemd.sh" >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv…"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

id "$SERVICE_USER" >/dev/null 2>&1 || \
    useradd --system --create-home --home-dir /var/lib/nc-music-bot "$SERVICE_USER"

echo "Installing into $APP_DIR…"
mkdir -p "$APP_DIR"
rsync -a --delete \
    --exclude .git --exclude .venv --exclude .env --exclude secrets \
    "$REPO_DIR/" "$APP_DIR/"

(cd "$APP_DIR" && uv sync --frozen --no-dev)

if [[ ! -f "$APP_DIR/.env" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "Created $APP_DIR/.env — EDIT IT before starting the service."
fi
chown -R "$SERVICE_USER" "$APP_DIR"

install -m 644 "$REPO_DIR/deploy/systemd/nc-music-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable nc-music-bot

cat <<EOF

Done. Next steps:
  1. Join this host to your tailnet:      tailscale up
  2. Create the bot's SSH key and install it on the destination:
         sudo -u $SERVICE_USER $APP_DIR/deploy/setup-ssh-key.sh root@<DEST_HOST>
  3. Edit $APP_DIR/.env (token, whitelist, destination)
  4. Verify:                              cd $APP_DIR && sudo -u $SERVICE_USER uv run python -m nc_music_bot --check
  5. Start:                               systemctl start nc-music-bot
EOF
