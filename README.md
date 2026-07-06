<h1 align="center">Telegram to Nextcloud music bot</h1>
<p align="center">
  <img src="assets/logo.png" alt="Telegram-to-Nextcloud-music-bot logo" width="200"/>
</p>

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
git clone https://github.com/Art4Man/nextcloud-music-telegram-bot && cd nextcloud-music-telegram-bot
uv sync

# 1. Create a bot: talk to @BotFather on Telegram, copy the token.

# 2. Set up SSH auth — pick one:
#    a) Key-based (recommended): copies a fresh key to the destination, asks for the
#       password once, then never again.
deploy/setup-ssh-key.sh root@<dest-tailnet-ip>
#    b) Password auth: skip this step, then set DEST_SSH_PASSWORD in .env instead of
#       DEST_SSH_KEY_PATH. Requires PasswordAuthentication yes in sshd_config on the
#       destination.

# 3. Configure:
cp .env.example .env   # fill in token, ALLOWED_USER_IDS, DEST_*, NEXTCLOUD_*

# 4. Verify everything but Telegram (no token needed):
uv run python -m nc_music_bot --check

# 5. Run:
uv run python -m nc_music_bot
```

Send `/myid` to the bot to learn your numeric Telegram ID for `ALLOWED_USER_IDS`.
Then send it any audio file. `/status` re-runs the destination health check from Telegram.

### Try it locally (stage mode)

No Tailscale, SSH, or Nextcloud — useful for hacking on the bot or testing a PR. Set
`APP_MODE=stage` and the only other settings you need are a bot token and `ALLOWED_USER_IDS`:

```bash
uv sync
printf 'APP_MODE=stage\nTELEGRAM_BOT_TOKEN=<token>\nALLOWED_USER_IDS=<your-id>\n' > .env
uv run python -m nc_music_bot --check   # confirms stage mode + a writable stage dir
uv run python -m nc_music_bot
```

Songs you send are copied into `<TEMP_DIR>/stage` (default `/tmp/nc-music-bot/stage`, override
with `STAGE_DIR`) and the bot replies `✅ Added <name>`. The stage directory is wiped clean on
every restart, so nothing accumulates.

### Auto-relaying another bot's audio

The bot can pick up MP3s another bot posts in a shared group (e.g. a Spotify
downloader) and upload them automatically — no command needed. This relies on
Telegram's Bot-to-Bot Communication feature:

1. In @BotFather, enable **Bot-to-Bot Communication Mode** for your bot.
2. Make the bot a **group admin**, or **disable its Group Privacy Mode**, so
   Telegram delivers other bots' messages to it.
3. Whitelist the source bot via `SOURCE_BOT_USERNAMES`, or at runtime with
   `/addbot @TheirBot`.

### Adding songs by replying in a group

**Reply** to a music message and **@mention** the bot — it grabs that song, uploads
it, runs the scan, and replies with the result. Only whitelisted users
(`ALLOWED_USER_IDS`) can trigger it. This works two ways:

**A. Bot is in the group.** Just reply to the song and `@mention` the bot. To keep
the group quiet, the bot replies **in your private chat (DM)** with live progress
(bar + percent) — nothing is posted in the group. You must have **started the bot in
a private chat first** (send it `/start` once); otherwise it can't DM you and the
upload is skipped (logged on the server).

**B. Bot is *not* in the group (Guest Mode).** Add a song from **any** chat the bot
isn't in, via Telegram's [Guest Mode](https://core.telegram.org/bots/features#guest-bots)
(Bot API 10.0, needs `python-telegram-bot>=22.8`):

1. Open your bot's settings in [@BotFather's MiniApp](https://t.me/Botfather?startapp)
   and enable **Guest Mode**.
2. In any chat, reply to a song and `@mention` the bot.

Guest Mode limits (from Telegram, not the bot):

- Telegram allows the bot **exactly one** reply per mention, so there's no live
  progress in the chat — just a single final message when the transfer finishes.
- As a convenience, if you've started the bot in a private chat, it also DMs you live
  progress. This is skipped silently if it can't reach you.

Either way, replying to something that isn't a supported audio file sends a short hint.

### Duplicate files

Before downloading anything, the bot derives the destination filename from the
message metadata (the sent file name, else the `artist - title` tags) and checks
whether it already exists in the library. If it does, you're asked what to do:

```
⚠️ song.mp3 already exists in the music library. What would you like to do?
[Upload anyway (rename)]  [Overwrite]  [Cancel]
```

- **Upload anyway (rename)** — add a number suffix (`song (1).mp3`).
- **Overwrite** — replace the existing file; the old one is removed only after the
  new content has fully arrived.
- **Cancel** — nothing is downloaded or uploaded.

No answer within `DUPLICATE_CHECK_TIMEOUT_SECS` (default 60 s) cancels the upload —
duplicates are **not added** unless you say so. The same applies when there's no way
to ask (a guest-mode upload without a reachable DM). Only the user who initiated the
upload can press the buttons; for audio auto-relayed from a source bot, any
whitelisted user can decide.

Known limitation: the existence check and the upload are separate SFTP operations,
so two simultaneous uploads of the same name can still race past each other (the
loser gets a number suffix).

### Whitelist management (admins)

Admins — the IDs listed in `ALLOWED_USER_IDS` — can manage both whitelists live:

- `/whitelist` — show allowed users and source bots
- `/allow <id> [id…]` / `/deny <id> [id…]` — manage allowed users
- `/addbot <@username> [@username…]` / `/rmbot <@username> [@username…]` — manage source bots

Changes apply immediately; set `WHITELIST_STORE_PATH` to persist them across restarts.

## Configuration reference

All settings come from environment variables or `.env` (see `.env.example` for the documented
template). Env vars override `.env`.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | to run | — | Token from @BotFather (`--check` works without it) |
| `ALLOWED_USER_IDS` | yes | — | Comma-separated numeric Telegram IDs allowed to use the bot |
| `APP_MODE` | no | `production` | `stage` copies uploads to a local dir instead of the Nextcloud destination (no Tailscale/SSH; all `DEST_*`/scan settings ignored) |
| `STAGE_DIR` | no | `<TEMP_DIR>/stage` | Stage-mode target directory; wiped on every restart |
| `SOURCE_BOT_USERNAMES` | no | — | Comma-separated bot @usernames whose audio is auto-relayed (needs Bot-to-Bot Communication Mode in @BotFather + the bot as group admin or with Group Privacy off) |
| `WHITELIST_STORE_PATH` | no | — | JSON file persisting runtime whitelist changes (`/allow`, `/addbot`, …); unset = in-memory only. Put on a mounted volume for Docker |
| `MAX_FILE_MB` | no | `2000` | Reject files larger than this |
| `TELEGRAM_API_BASE_URL` | no | — | Self-hosted telegram-bot-api URL; unlocks 2 GB downloads |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | large mode | — | Used by the bot-api helper container only |
| `DEST_HOST` | production | — | Tailnet IP or MagicDNS name of the destination |
| `DEST_SSH_PORT` | no | `22` | SSH port |
| `DEST_SSH_USER` | no | `root` | SSH user (snap Nextcloud's `occ` requires root) |
| `DEST_SSH_KEY_PATH` | one of | — | Private key path (recommended); Docker installs expect `/secrets/nc-music-bot_ed25519` |
| `DEST_SSH_PASSWORD` | one of | — | Password auth alternative — set this instead of `DEST_SSH_KEY_PATH`; requires `PasswordAuthentication yes` in `sshd_config` on the destination |
| `DEST_KNOWN_HOSTS` | no | pin on first connect | known_hosts file for host-key verification |
| `DEST_PATH` | production | — | Absolute upload directory on the destination |
| `RUN_SCAN` | no | `true` | Run occ scans after upload (off = plain SFTP drop) |
| `NEXTCLOUD_OCC` | no | `nextcloud.occ` | occ command (snap default; else e.g. `php /var/www/nextcloud/occ`) |
| `NEXTCLOUD_USER` | no | `admin` | Nextcloud user owning the library |
| `NEXTCLOUD_SCAN_PATH` | if scanning | — | `files:scan --path=` value, e.g. `admin/files/Music` |
| `DUPLICATE_CHECK_TIMEOUT_SECS` | no | `60` | How long the duplicate prompt (rename/overwrite/cancel) waits before auto-cancelling |
| `TEMP_DIR` | no | `<tmp>/nc-music-bot` | Staging dir on the bot host |
| `LOG_LEVEL` | no | `INFO` | Python log level |

Typical home-server values (snap Nextcloud):
`DEST_PATH=/var/snap/nextcloud/common/nextcloud/data/admin/files/Music`,
`NEXTCLOUD_SCAN_PATH=admin/files/Music`, `NEXTCLOUD_OCC=nextcloud.occ`, `DEST_SSH_USER=root`.

## Deploy

### One-command install (Docker, recommended)

On any internet-connected Linux server:

```bash
git clone https://github.com/Art4Man/nextcloud-music-telegram-bot && cd nextcloud-music-telegram-bot
sudo ./install.sh
```

If the repository is public (or git auth is set up on the server), the clone step can be
skipped — the installer fetches the source itself:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/Art4Man/nextcloud-music-telegram-bot/main/install.sh)"
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
- SSH auth via a dedicated key (recommended) or password (`DEST_SSH_PASSWORD`); `.env`, keys, and `secrets/` are gitignored.
- The bot host stores nothing: temp files are deleted in a `finally`, success or failure.

## Dedicated SSH user on the destination (recommended)

The default config uses `root`, which works but gives the bot more access than it needs.
A better approach is a dedicated system user that can only write to the music directory and
run the two `occ` scan commands.

### 1. Create the user

Run these on the **destination** (home server):

```bash
sudo useradd --system --create-home --shell /bin/bash nc-music-bot
```

### 2. Grant write access to the music directory

```bash
# Snap Nextcloud (adjust path if your library lives elsewhere):
sudo chown nc-music-bot:nc-music-bot \
    /var/snap/nextcloud/common/nextcloud/data/admin/files/Music
```

### 3. Allow passwordless occ (only if RUN_SCAN=true)

`occ` requires root on snap Nextcloud. A narrow `sudoers` entry lets the bot run it
without a full root shell:

```bash
sudo tee /etc/sudoers.d/nc-music-bot <<'EOF'
nc-music-bot ALL=(root) NOPASSWD: /snap/bin/nextcloud.occ
EOF
sudo chmod 440 /etc/sudoers.d/nc-music-bot
```

Then tell the bot to prefix the command with `sudo`:

```env
DEST_SSH_USER=nc-music-bot
NEXTCLOUD_OCC=sudo nextcloud.occ
```

If you are **not** running occ scans (`RUN_SCAN=false`), skip this step entirely —
the user only needs write access to `DEST_PATH`.

### 4. Generate and install an SSH key

```bash
# On the bot host (or wherever you have ssh-keygen):
ssh-keygen -t ed25519 -N "" -C "nc-music-bot@$(hostname)" -f ~/.ssh/nc-music-bot_ed25519

# Copy the public key to the destination:
ssh-copy-id -i ~/.ssh/nc-music-bot_ed25519.pub nc-music-bot@<dest-tailnet-ip>
```

Or use the helper script from a checkout of this repo (does the same thing):

```bash
deploy/setup-ssh-key.sh nc-music-bot@<dest-tailnet-ip>
```

### 5. Update .env

```env
DEST_SSH_USER=nc-music-bot
DEST_SSH_KEY_PATH=/secrets/nc-music-bot_ed25519   # Docker path
```

Run `nc-music-bot check` (or `uv run python -m nc_music_bot --check`) to verify
everything before restarting.

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
uv run pytest          # 54 tests, asyncssh fully mocked
uv run mypy            # strict
uv run ruff check && uv run ruff format --check
```

Project layout and conventions are in `CLAUDE.md` (gitignored, local dev only).

## License

MIT — see [LICENSE](LICENSE).
