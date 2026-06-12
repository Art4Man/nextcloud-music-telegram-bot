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

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Send me an audio file and I'll add it to the music library.\n\n"
    "/myid — show your Telegram user ID (for the whitelist)\n"
    "/status — check the connection to the music server\n"
    "/help — this message"
)


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return cast(Settings, context.bot_data["settings"])


def _destination(context: ContextTypes.DEFAULT_TYPE) -> NextcloudDestination:
    return cast(NextcloudDestination, context.bot_data["destination"])


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(HELP_TEXT)


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
        remote_name = await destination.upload(local, filename)
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
    log.warning("Rejected audio from non-whitelisted user %s", user.id)
    await message.reply_text(
        f"Not authorized. Your Telegram ID is {user.id} — "
        "it must be added to ALLOWED_USER_IDS before you can use this bot."
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception while processing an update", exc_info=context.error)
