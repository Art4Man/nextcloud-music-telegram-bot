"""Telegram command handlers + the receive→upload→scan pipeline."""

import logging
import shutil
from typing import cast

from telegram import Update
from telegram.ext import ContextTypes

from .config import Settings
from .destination import NextcloudDestination
from .download import download_media
from .errors import UserFacingError
from .progress import UploadProgressReporter
from .whitelist import Whitelist

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Send me an audio file and I'll add it to the music library.\n\n"
    "/myid — show your Telegram user ID (for the whitelist)\n"
    "/status — check the connection to the music server\n"
    "/help — this message"
)

ADMIN_HELP_TEXT = (
    "Whitelist management (admins only):\n"
    "/whitelist — show allowed users and source bots\n"
    "/allow <id> [id…] — allow user IDs\n"
    "/deny <id> [id…] — remove user IDs\n"
    "/addbot <@username> [@username…] — auto-relay a bot's audio\n"
    "/rmbot <@username> [@username…] — stop relaying a bot's audio"
)


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return cast(Settings, context.bot_data["settings"])


def _destination(context: ContextTypes.DEFAULT_TYPE) -> NextcloudDestination:
    return cast(NextcloudDestination, context.bot_data["destination"])


def _whitelist(context: ContextTypes.DEFAULT_TYPE) -> Whitelist:
    return cast(Whitelist, context.bot_data["whitelist"])


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = HELP_TEXT
    user = update.effective_user
    if user and _whitelist(context).is_admin(user.id):
        text = f"{text}\n\n{ADMIN_HELP_TEXT}"
    await message.reply_text(text)


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_user:
        await update.effective_message.reply_text(
            f"Your Telegram user ID: {update.effective_user.id}"
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    status = await message.reply_text("Running checks…")
    items = await _destination(context).check()
    lines = [
        f"{'✅' if item.ok else '❌'} {item.label}" + (f" — {item.detail}" if item.detail else "")
        for item in items
    ]
    await status.edit_text("\n".join(lines))


async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return False
    if not _whitelist(context).is_admin(user.id):
        await message.reply_text("Not authorized — only admins can manage the whitelist.")
        return False
    return True


def _parse_user_ids(args: list[str]) -> tuple[list[int], list[str]]:
    ids: list[int] = []
    bad: list[str] = []
    for arg in args:
        try:
            ids.append(int(arg))
        except ValueError:
            bad.append(arg)
    return ids, bad


async def cmd_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    message = update.effective_message
    assert message is not None
    wl = _whitelist(context)
    users = ", ".join(str(uid) for uid in wl.list_users()) or "(none)"
    bots = ", ".join(f"@{name}" for name in wl.list_bots()) or "(none)"
    await message.reply_text(f"Allowed users: {users}\nSource bots: {bots}")


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    message = update.effective_message
    assert message is not None
    ids, bad = _parse_user_ids(context.args or [])
    if not ids and not bad:
        await message.reply_text("Usage: /allow <user_id> [user_id…]")
        return
    wl = _whitelist(context)
    added = [uid for uid in ids if wl.allow_user(uid)]
    lines = []
    if added:
        lines.append("Allowed: " + ", ".join(str(uid) for uid in added))
    already = [uid for uid in ids if uid not in added]
    if already:
        lines.append("Already allowed: " + ", ".join(str(uid) for uid in already))
    if bad:
        lines.append("Not numeric IDs: " + ", ".join(bad))
    await message.reply_text("\n".join(lines))


async def cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    message = update.effective_message
    assert message is not None
    ids, bad = _parse_user_ids(context.args or [])
    if not ids and not bad:
        await message.reply_text("Usage: /deny <user_id> [user_id…]")
        return
    wl = _whitelist(context)
    removed: list[int] = []
    skipped: list[str] = []
    for uid in ids:
        try:
            if wl.deny_user(uid):
                removed.append(uid)
            else:
                skipped.append(f"{uid} (not in list)")
        except ValueError as exc:
            skipped.append(f"{uid} — {exc}")
    lines = []
    if removed:
        lines.append("Removed: " + ", ".join(str(uid) for uid in removed))
    if skipped:
        lines.append("Skipped: " + "; ".join(skipped))
    if bad:
        lines.append("Not numeric IDs: " + ", ".join(bad))
    await message.reply_text("\n".join(lines))


async def cmd_addbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    message = update.effective_message
    assert message is not None
    args = context.args or []
    if not args:
        await message.reply_text("Usage: /addbot <@username> [@username…]")
        return
    wl = _whitelist(context)
    added = [name for arg in args if (name := wl.add_bot(arg))]
    if added:
        await message.reply_text("Now relaying audio from: " + ", ".join(f"@{n}" for n in added))
    else:
        await message.reply_text("Nothing added (already present or empty).")


async def cmd_rmbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    message = update.effective_message
    assert message is not None
    args = context.args or []
    if not args:
        await message.reply_text("Usage: /rmbot <@username> [@username…]")
        return
    wl = _whitelist(context)
    removed = [name for arg in args if (name := wl.remove_bot(arg))]
    if removed:
        names = ", ".join(f"@{n}" for n in removed)
        await message.reply_text(f"Stopped relaying audio from: {names}")
    else:
        await message.reply_text("Nothing removed (not in the list).")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The whole pipeline: ack → download → SFTP upload → occ scan → reply.

    Stateless by construction — the temp directory is removed in `finally`,
    success or not.
    """
    message = update.effective_message
    if message is None:
        return
    uid = update.effective_user.id if update.effective_user else "unknown"
    settings = _settings(context)
    destination = _destination(context)
    status = await message.reply_text("⬇️ Receiving…")
    workdir = None
    try:
        local, filename = await download_media(message, settings)
        workdir = local.parent
        log.info("Upload started — user %s: %s", uid, filename)
        await status.edit_text(f"📤 Uploading {filename} …")
        reporter = UploadProgressReporter(status, filename)
        try:
            remote_name = await destination.upload(local, filename, progress=reporter)
        finally:
            await reporter.finish()
        reply = f"✅ Added {remote_name}"
        if settings.run_scan:
            await status.edit_text(f"🔍 Indexing {remote_name} …")
            scan = await destination.scan()
            if not scan.ok:
                reply = f"⚠️ Uploaded {remote_name}, but the library scan failed: {scan.detail}"
            elif scan.new_tracks is not None:
                reply = f"{reply} — {scan.new_tracks} track(s) indexed"
        log.info("Transfer complete — user %s: %s", uid, remote_name)
        await status.edit_text(reply)
    except UserFacingError as exc:
        log.warning("Transfer failed — user %s: %s", uid, exc)
        await status.edit_text(f"❌ {exc}")
    except Exception:
        log.exception("Transfer failed — user %s", uid)
        await status.edit_text("❌ Transfer failed — see the bot logs for details.")
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


async def handle_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if message is None or user is None:
        return
    if user.is_bot:
        log.info("Ignoring audio from non-whitelisted bot %s (@%s)", user.id, user.username)
        return
    log.warning("Rejected audio from non-whitelisted user %s", user.id)
    await message.reply_text(
        f"Not authorized. Your Telegram ID is {user.id} — "
        "it must be added to ALLOWED_USER_IDS before you can use this bot."
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception while processing an update", exc_info=context.error)
