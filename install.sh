#!/usr/bin/env bash
# nc-music-bot — one-command interactive installer (Docker-based).
#
# Run on any internet-connected Linux server, from a checkout of this repo:
#     sudo ./install.sh
# (or, if the repo is reachable for you anonymously / via configured git auth:
#     bash <(curl -fsSL https://raw.githubusercontent.com/Art4Man/nextcloud-music-telegram-bot/main/install.sh) )
#
# What it does, in order:
#   1. installs missing dependencies (curl/ssh/rsync/git via your package manager,
#      Docker via get.docker.com, Tailscale via tailscale.com/install.sh)
#   2. joins this server to your tailnet (interactive login URL)
#   3. asks for the bot token, user whitelist, and destination details
#   4. generates a dedicated SSH key and installs it on the destination
#   5. health-checks the destination, then starts the bot with docker compose
#   6. installs the `nc-music-bot` management command (logs/restart/update/…)
#
# Re-running is safe: existing config and keys are kept unless you choose otherwise.
set -euo pipefail

APP_DIR=${NC_MUSIC_BOT_DIR:-/opt/nc-music-bot}
REPO_URL=${NC_MUSIC_BOT_REPO:-https://github.com/Art4Man/nextcloud-music-telegram-bot.git}
DEFAULT_DEST_PATH=/var/snap/nextcloud/common/nextcloud/data/admin/files/Music
KEY_NAME=nc-music-bot_ed25519

say()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

# All prompts read from /dev/tty so the script also works when piped from curl.
ask() {
    local prompt="$1" default="${2:-}" answer=""
    if [[ -n "$default" ]]; then
        read -rp "$prompt [$default]: " answer </dev/tty || true
    else
        read -rp "$prompt: " answer </dev/tty || true
    fi
    printf '%s' "${answer:-$default}"
}

ask_required() {
    local prompt="$1" pattern="${2:-..*}" hint="${3:-a value is required}" answer
    while :; do
        answer="$(ask "$prompt")"
        [[ "$answer" =~ $pattern ]] && { printf '%s' "$answer"; return; }
        warn "$hint"
    done
}

ask_yn() {
    local question="$1" default="${2:-n}" answer
    answer="$(ask "$question (y/n)" "$default")"
    [[ "$answer" =~ ^[Yy] ]]
}

env_get() { sed -n "s/^$1=//p" "$APP_DIR/.env" 2>/dev/null | tail -1; }

require_root() {
    [[ "$(uname -s)" == "Linux" ]] || die "This installer targets Linux servers."
    [[ $EUID -eq 0 ]] || die "Run as root: sudo ./install.sh"
}

install_base_deps() {
    if command -v curl >/dev/null && command -v ssh-keygen >/dev/null \
        && command -v ssh-copy-id >/dev/null && command -v rsync >/dev/null; then
        return
    fi
    say "Installing base dependencies"
    if command -v apt-get >/dev/null; then
        apt-get update -y && apt-get install -y curl ca-certificates openssh-client rsync git
    elif command -v dnf >/dev/null; then
        dnf install -y curl ca-certificates openssh-clients rsync git
    elif command -v yum >/dev/null; then
        yum install -y curl ca-certificates openssh-clients rsync git
    elif command -v pacman >/dev/null; then
        pacman -Sy --noconfirm curl ca-certificates openssh rsync git
    elif command -v zypper >/dev/null; then
        zypper --non-interactive install curl ca-certificates openssh rsync git
    elif command -v apk >/dev/null; then
        apk add --no-cache curl ca-certificates openssh-client rsync git bash
    else
        warn "Unknown package manager — make sure curl, ssh-keygen, ssh-copy-id, rsync and git are available."
    fi
}

ensure_docker() {
    if ! command -v docker >/dev/null; then
        say "Installing Docker"
        curl -fsSL https://get.docker.com | sh
    fi
    command -v systemctl >/dev/null && systemctl enable --now docker >/dev/null 2>&1 || true
    docker compose version >/dev/null 2>&1 \
        || die "The docker compose plugin is missing — install docker-compose-plugin and re-run."
    ok "Docker $(docker --version | sed 's/Docker version //;s/,.*//')"
}

ensure_tailscale() {
    if ! command -v tailscale >/dev/null; then
        say "Installing Tailscale"
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
    if ! tailscale status >/dev/null 2>&1; then
        say "Joining the tailnet — open the login URL below in a browser"
        tailscale up </dev/tty
    fi
    ok "Tailscale up — this host: $(tailscale ip -4 2>/dev/null | head -1)"
}

stage_source() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /)"
    mkdir -p "$APP_DIR"
    if [[ -f "$script_dir/docker-compose.yml" && -f "$script_dir/pyproject.toml" ]]; then
        if [[ "$script_dir" != "$APP_DIR" ]]; then
            say "Installing source into $APP_DIR"
            rsync -a --exclude .venv --exclude .env --exclude secrets "$script_dir/" "$APP_DIR/"
        fi
    elif [[ -f "$APP_DIR/docker-compose.yml" ]]; then
        ok "Using existing install in $APP_DIR (update later with: nc-music-bot update)"
    else
        say "Cloning $REPO_URL"
        git clone "$REPO_URL" "$APP_DIR" </dev/tty \
            || die "Clone failed — check that git can reach $REPO_URL (or set NC_MUSIC_BOT_REPO=<url>)."
    fi
}

configure() {
    cd "$APP_DIR"
    if [[ -f .env ]] && ! ask_yn "Existing configuration found — reconfigure?"; then
        ok "Keeping current .env"
        return
    fi

    say "Telegram"
    local token me bot_user ids
    while :; do
        token="$(ask_required "Bot token (from @BotFather)" \
            '^[0-9]+:[A-Za-z0-9_-]+$' "that does not look like a bot token (123456:ABC…)")"
        me="$(curl -sm 10 "https://api.telegram.org/bot${token}/getMe" || true)"
        if grep -q '"ok":true' <<<"$me"; then
            bot_user="$(sed -n 's/.*"username":"\([^"]*\)".*/\1/p' <<<"$me")"
            ok "Token accepted — your bot is @${bot_user}"
            break
        fi
        warn "Telegram rejected that token (or it is unreachable)."
        ask_yn "Use it anyway?" && break
    done
    ids="$(ask_required "Allowed Telegram user IDs, comma-separated (tip: @userinfobot tells you yours)" \
        '^[0-9]+([[:space:],]+[0-9]+)*$' "numbers separated by commas, e.g. 12345678")"

    say "Destination (over Tailscale)"
    echo "Your tailnet currently knows these peers:"
    tailscale status 2>/dev/null | awk 'NR>1 && NF {print "   " $1 "  " $2}' | head -8 || true
    local dest_host dest_user dest_port dest_path
    dest_host="$(ask_required "Destination tailnet IP or MagicDNS name")"
    dest_user="$(ask "SSH user on the destination" "root")"
    dest_port="$(ask "SSH port" "22")"
    dest_path="$(ask "Upload directory on the destination" "$DEFAULT_DEST_PATH")"

    say "Nextcloud scan"
    local run_scan=true occ nc_user scan_path scan_default
    if ask_yn "Run Nextcloud occ scans after each upload? (choose n for a plain SFTP drop)" "y"; then
        scan_default="admin/files/Music"
        [[ "$dest_path" == */data/*/files* ]] && scan_default="${dest_path#*/data/}"
        occ="$(ask "occ command" "nextcloud.occ")"
        scan_path="$(ask "files:scan path" "$scan_default")"
        nc_user="$(ask "Nextcloud user owning the library" "${scan_default%%/*}")"
    else
        run_scan=false occ=nextcloud.occ nc_user=admin scan_path=
    fi

    say "Large files"
    local profiles="" api_base="" api_id="" api_hash=""
    if ask_yn "Accept files over 20 MB (runs a local telegram-bot-api container, needs my.telegram.org credentials)?"; then
        api_id="$(ask_required "TELEGRAM_API_ID (from https://my.telegram.org)" '^[0-9]+$' "numeric api_id")"
        api_hash="$(ask_required "TELEGRAM_API_HASH" '^[0-9a-f]{16,}$' "hex api_hash from my.telegram.org")"
        api_base="http://127.0.0.1:8081"
        profiles="large"
    fi

    umask 077
    {
        echo "# Generated by install.sh on $(date -u +%Y-%m-%d) — edit with: nc-music-bot edit"
        echo "TELEGRAM_BOT_TOKEN=$token"
        echo "ALLOWED_USER_IDS=$ids"
        echo "DEST_HOST=$dest_host"
        echo "DEST_SSH_USER=$dest_user"
        echo "DEST_SSH_PORT=$dest_port"
        echo "DEST_SSH_KEY_PATH=/secrets/$KEY_NAME"
        echo "DEST_PATH=$dest_path"
        echo "RUN_SCAN=$run_scan"
        if [[ "$run_scan" == true ]]; then
            echo "NEXTCLOUD_OCC=$occ"
            echo "NEXTCLOUD_USER=$nc_user"
            echo "NEXTCLOUD_SCAN_PATH=$scan_path"
        fi
        if [[ -n "$profiles" ]]; then
            echo "COMPOSE_PROFILES=$profiles"
            echo "TELEGRAM_API_BASE_URL=$api_base"
            echo "TELEGRAM_API_ID=$api_id"
            echo "TELEGRAM_API_HASH=$api_hash"
        fi
    } > .env
    ok "Wrote $APP_DIR/.env"
}

setup_ssh_key() {
    cd "$APP_DIR"
    mkdir -p secrets
    local key="secrets/$KEY_NAME"
    if [[ ! -f "$key" ]]; then
        ssh-keygen -t ed25519 -N "" -C "nc-music-bot@$(hostname)" -f "$key" -q
        ok "Generated SSH key $APP_DIR/$key"
    fi
    # The bot container runs as uid 1000; secrets are created by root, so fix ownership.
    chown -R 1000:1000 "$APP_DIR/secrets/"
    local host user port
    host="$(env_get DEST_HOST)" user="$(env_get DEST_SSH_USER)" port="$(env_get DEST_SSH_PORT)"
    if ask_yn "Install the bot's public key on $user@$host now? (asks for that account's password once)" "y"; then
        if ssh-copy-id -i "$key.pub" -p "${port:-22}" "$user@$host" </dev/tty; then
            ok "Key installed on $host"
        else
            warn "ssh-copy-id failed. Append this line to ~/.ssh/authorized_keys on the destination yourself:"
            echo
            cat "$key.pub"
            echo
            ask_yn "Continue anyway?" || die "Aborted — re-run sudo ./install.sh when the key is in place."
        fi
    fi
}

install_cli() {
    cat > /usr/local/bin/nc-music-bot <<'CLI'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR=__APP_DIR__
cd "$APP_DIR"
cmd="${1:-help}"
shift || true
case "$cmd" in
    up|start)   docker compose up -d "$@" ;;
    down|stop)  docker compose down "$@" ;;
    restart)    docker compose restart "$@" ;;
    logs)       docker compose logs -f --tail=100 bot ;;
    status|ps)  docker compose ps ;;
    check)      docker compose run --rm --no-deps bot python -m nc_music_bot check ;;
    edit)       "${EDITOR:-nano}" .env && docker compose up -d --force-recreate bot ;;
    update)     git pull --ff-only && docker compose build && docker compose up -d ;;
    uninstall)
        read -rp "Stop the bot and delete $APP_DIR? [y/N] " answer
        [[ "$answer" =~ ^[Yy] ]] || exit 1
        docker compose down --volumes --remove-orphans || true
        rm -rf "$APP_DIR" /usr/local/bin/nc-music-bot
        echo "Removed. (Docker and Tailscale were left installed.)"
        ;;
    *)
        cat <<'USAGE'
nc-music-bot — manage the Telegram → Nextcloud music relay

  nc-music-bot up         start the bot
  nc-music-bot down       stop and remove the containers
  nc-music-bot restart    restart
  nc-music-bot logs       follow the bot logs
  nc-music-bot status     container status
  nc-music-bot check      run the destination health check (SSH, occ, write test)
  nc-music-bot edit       edit .env, then recreate the bot
  nc-music-bot update     pull the latest code, rebuild, restart
  nc-music-bot uninstall  remove everything (asks first)
USAGE
        ;;
esac
CLI
    sed -i "s|__APP_DIR__|$APP_DIR|" /usr/local/bin/nc-music-bot
    chmod +x /usr/local/bin/nc-music-bot
    ok "Installed management command: nc-music-bot"
}

deploy() {
    cd "$APP_DIR"
    say "Building the bot image"
    docker compose build bot
    say "Checking the destination over the tailnet"
    if docker compose run --rm --no-deps bot python -m nc_music_bot check; then
        ok "Destination healthy"
    else
        warn "The health check failed — usually DEST_HOST/key/occ settings."
        ask_yn "Start the bot anyway?" || die "Aborted — fix with: nc-music-bot edit, then: nc-music-bot up"
    fi
    say "Starting"
    docker compose up -d
    docker compose ps
}

summary() {
    local bot_user
    bot_user="$(curl -sm 10 "https://api.telegram.org/bot$(env_get TELEGRAM_BOT_TOKEN)/getMe" 2>/dev/null \
        | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
    say "Done!"
    cat <<EOF
Send an audio file to @${bot_user:-your-bot} on Telegram — it should land in your
Nextcloud library and be indexed within seconds.

Manage the bot with:  nc-music-bot {up|down|restart|logs|status|check|edit|update|uninstall}
Config:               $APP_DIR/.env
EOF
}

main() {
    printf '\033[1m\n  nc-music-bot installer — Telegram → Nextcloud relay over Tailscale\n\033[0m'
    require_root
    install_base_deps
    ensure_docker
    ensure_tailscale
    stage_source
    configure
    setup_ssh_key
    install_cli
    deploy
    summary
}

main "$@"
