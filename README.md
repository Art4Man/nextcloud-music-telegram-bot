# nc-music-bot

Send a song to a Telegram bot → it lands in your home Nextcloud music library → the library
is rescanned automatically → the track shows up in Amperfy (or any Subsonic client) right away.

The bot is a **portable, stateless relay**. It runs on *any* host with internet access (VPS,
Oracle Cloud Always Free, a laptop) and pushes files over **Tailscale** into a destination you
configure at deploy time. Every file is deleted from the bot host the moment the transfer
finishes.

## Why a relay?

The home server hosting Nextcloud has **no general internet access** — Telegram is blocked
there, so a bot could never run on it. But Tailscale is direct WireGuard between nodes: it
doesn't need the destination to have open internet. So the bot lives wherever internet is
available and reaches home strictly over the tailnet.

```
[Telegram] --public internet--> [ BOT HOST (any machine with internet) ]
                                     |  python-telegram-bot (long polling)
                                     |  user-ID whitelist · filename sanitization
                                     |  download to temp (20 MB cloud API,
                                     |    or up to 2 GB via local bot-api helper)
                                     v
                              [ Tailscale tailnet ]
                                     |  asyncssh: SFTP upload -> DEST_PATH
                                     |  asyncssh: occ files:scan + music:scan
                                     v
                       [ HOME NEXTCLOUD SERVER (tailnet node, no internet) ]
                                     |
                                     v
                       temp copy deleted -> success reply on Telegram
```

## Quick start

Prereqs on the bot host: Python 3.12+, [uv](https://docs.astral.sh/uv/),
[Tailscale](https://tailscale.com) joined to the same tailnet as the destination.

```bash
git clone <this repo> && cd nextcloud-music-telegram-bot
uv sync

# 1. Create a bot: talk to @BotFather on Telegram, copy the token.

# 2. Give the bot an SSH key on the destination (asks for the password once):
deploy/setup-ssh-key.sh root@<dest-tailnet-ip>

# 3. Configure:
cp .env.example .env   # fill in token, ALLOWED_USER_IDS, DEST_*, NEXTCLOUD_*

# 4. Verify everything but Telegram (no token needed):
uv run python -m nc_music_bot --check

# 5. Run:
uv run python -m nc_music_bot
```

Send `/myid` to the bot to learn your numeric Telegram ID for `ALLOWED_USER_IDS`.
Then send it any audio file. `/status` re-runs the destination health check from Telegram.

## Configuration reference

All settings come from environment variables or `.env` (see `.env.example` for the documented
template). Env vars override `.env`.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | to run | — | Token from @BotFather (`--check` works without it) |
| `ALLOWED_USER_IDS` | yes | — | Comma-separated numeric Telegram IDs allowed to use the bot |
| `MAX_FILE_MB` | no | `2000` | Reject files larger than this |
| `TELEGRAM_API_BASE_URL` | no | — | Self-hosted telegram-bot-api URL; unlocks 2 GB downloads |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | large mode | — | Used by the bot-api helper container only |
| `DEST_HOST` | yes | — | Tailnet IP or MagicDNS name of the destination |
| `DEST_SSH_PORT` | no | `22` | SSH port |
| `DEST_SSH_USER` | no | `root` | SSH user (snap Nextcloud's `occ` requires root) |
| `DEST_SSH_KEY_PATH` | one of | — | Private key path (recommended) |
| `DEST_SSH_PASSWORD` | one of | — | Password auth fallback (discouraged) |
| `DEST_KNOWN_HOSTS` | no | pin on first connect | known_hosts file for host-key verification |
| `DEST_PATH` | yes | — | Absolute upload directory on the destination |
| `RUN_SCAN` | no | `true` | Run occ scans after upload (off = plain SFTP drop) |
| `NEXTCLOUD_OCC` | no | `nextcloud.occ` | occ command (snap default; else e.g. `php /var/www/nextcloud/occ`) |
| `NEXTCLOUD_USER` | no | `admin` | Nextcloud user owning the library |
| `NEXTCLOUD_SCAN_PATH` | if scanning | — | `files:scan --path=` value, e.g. `admin/files/Music` |
| `TEMP_DIR` | no | `<tmp>/nc-music-bot` | Staging dir on the bot host |
| `LOG_LEVEL` | no | `INFO` | Python log level |

Typical home-server values (snap Nextcloud):
`DEST_PATH=/var/snap/nextcloud/common/nextcloud/data/admin/files/Music`,
`NEXTCLOUD_SCAN_PATH=admin/files/Music`, `NEXTCLOUD_OCC=nextcloud.occ`, `DEST_SSH_USER=root`.

## Deploy

### One-command install (Docker, recommended)

On any internet-connected Linux server:

```bash
git clone <this repo> && cd nextcloud-music-telegram-bot
sudo ./install.sh
```

If the repository is public (or git auth is set up on the server), the clone step can be
skipped — the installer fetches the source itself:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/<you>/nextcloud-music-telegram-bot/main/install.sh)"
```

(For a private repo this one-liner can't download the script or clone, so use the
clone-then-run flow above.)

The interactive installer handles everything end to end: it installs whatever is missing
(Docker via get.docker.com, Tailscale via tailscale.com/install.sh, ssh/rsync via your package
manager), joins the server to your tailnet, validates your bot token live against Telegram,
asks for the whitelist and destination details (with sensible defaults), generates a dedicated
SSH key and installs it on the destination, runs the health check, and starts the bot with
docker compose. Re-running it is safe — existing config and keys are kept unless you say
otherwise.

It also installs a management command:

```
nc-music-bot up | down | restart | logs | status | check | edit | update | uninstall
```

Oracle Cloud Always Free works fine: create an Always Free `VM.Standard.E2.1.Micro` (or A1)
Ubuntu instance and run the two commands above. The bot only makes *outbound* connections
(Telegram HTTPS + tailnet), so no ingress rules are needed.

### Without Docker (systemd)

Prefer no containers? `deploy/install-systemd.sh` provisions a native install instead:
a dedicated system user, a uv-managed virtualenv under `/opt/nc-music-bot`, and a hardened
systemd unit.

```bash
sudo tailscale up                    # join the tailnet
sudo deploy/install-systemd.sh       # then follow the printed next steps (key, .env, --check, start)
journalctl -u nc-music-bot -f        # logs
```

### Docker, manual

The compose file uses **host networking** so containers reach the tailnet through the host's
tailscaled — install Tailscale on the Docker host and `tailscale up` first.

```bash
deploy/setup-ssh-key.sh root@<dest>           # key lands in ./secrets/
cp .env.example .env                          # set DEST_SSH_KEY_PATH=/secrets/nc-music-bot_ed25519
docker compose up -d --build
```

If you can't use host networking, run a `tailscale/tailscale` sidecar with a `TS_AUTHKEY` and
`network_mode: service:tailscale` on the bot — see the Tailscale Docker docs.

### Large files (up to 2 GB)

The standard Bot API caps *bot downloads* at **20 MB**. To accept big files, run the
self-hosted `telegram-bot-api` helper next to the bot:

1. Get `api_id`/`api_hash` at <https://my.telegram.org> → API development tools.
2. In `.env`: set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and
   `TELEGRAM_API_BASE_URL=http://127.0.0.1:8081`.
3. `docker compose --profile large up -d`

Note: the first time the bot connects through the helper, Telegram moves the bot off the cloud
API automatically. To go back, call the cloud API's `logOut` method for your bot.

## Security model

- Whitelist enforced on every handler; strangers get told their ID and nothing else.
- Filenames are sanitized to a single path component (no traversal out of `DEST_PATH`);
  uploads stage as `.part` and rename only when complete.
- SSH host key is pinned (first-connect TOFU by default, or provide `DEST_KNOWN_HOSTS`).
- Dedicated SSH key for the bot; `.env`, keys, and `secrets/` are gitignored.
- The bot host stores nothing: temp files are deleted in a `finally`, success or failure.

## Amperfy / Subsonic

After a successful upload the bot runs `occ files:scan` (so Nextcloud sees the file) and
`occ music:scan` (so the Music app indexes it). In Amperfy, pull-to-refresh the library —
new tracks appear without any server-side clicking.

## Troubleshooting

- **`--check` fails at "SSH connection"** — Is the bot host on the tailnet (`tailscale status`)?
  Is `DEST_HOST` the tailnet address, not the LAN one? Does the key exist and match
  `authorized_keys` on the destination?
- **Host key changed error** — The destination was reinstalled or you're being intercepted.
  If it's legit, delete the pinned entry (`~/.config/nc-music-bot/known_hosts` or your
  `DEST_KNOWN_HOSTS` file) and reconnect.
- **"File is X MB … caps bot downloads at 20 MB"** — Expected without the large-file helper;
  see *Large files* above.
- **Upload ok, but track missing in Amperfy** — Run `nextcloud.occ music:scan admin` manually
  on the server and check its output; verify `NEXTCLOUD_SCAN_PATH` matches where `DEST_PATH`
  lives inside the user's files.
- **Bot doesn't answer at all** — Check `journalctl -u nc-music-bot -f` (or
  `docker compose logs -f bot`); a wrong token fails at startup, a missing whitelist entry
  answers only `/myid`.

## Development

```bash
uv sync
uv run pytest          # 46 tests, asyncssh fully mocked
uv run mypy            # strict
uv run ruff check && uv run ruff format --check
```

Project layout and conventions: see [CLAUDE.md](CLAUDE.md).

## License

MIT — see [LICENSE](LICENSE).
