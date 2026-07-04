"""Telegram command handlers + the receive→upload→scan pipeline."""

import asyncio
import contextlib
import logging
import shutil
import uuid
from typing import cast

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    MessageEntity,
    Update,
    User,
)
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from .config import Settings
from .destination import Destination
from .download import download_media, is_supported_audio, resolve_filename
from .duplicate import (
    DuplicateChoice,
    DuplicatePrompts,
    ResolveOutcome,
    build_callback_data,
    parse_callback_data,
)
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

GUEST_HINT_TEXT = (
    "I can only add audio files. Reply to a song — sent as music or as a file "
    "(.mp3, .flac, .m4a, .ogg, …) — and mention me to add it to the library."
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


def _destination(context: ContextTypes.DEFAULT_TYPE) -> Destination:
    return cast(Destination, context.bot_data["destination"])


def _whitelist(context: ContextTypes.DEFAULT_TYPE) -> Whitelist:
    return cast(Whitelist, context.bot_data["whitelist"])


def _duplicate_prompts(context: ContextTypes.DEFAULT_TYPE) -> DuplicatePrompts:
    return cast(DuplicatePrompts, context.bot_data["duplicate_prompts"])


def describe_message(message: Message) -> str:
    """A short, log-safe description of what a message carries."""
    if message.audio is not None:
        a = message.audio
        return f"audio file_name={a.file_name!r} mime={a.mime_type} size={a.file_size}"
    if message.document is not None:
        d = message.document
        return f"document file_name={d.file_name!r} mime={d.mime_type} size={d.file_size}"
    if message.voice is not None:
        return f"voice size={message.voice.file_size}"
    if message.video is not None:
        return f"video size={message.video.file_size}"
    if message.photo:
        return "photo"
    if message.sticker is not None:
        return "sticker"
    if message.animation is not None:
        return "animation"
    if message.text is not None:
        text = message.text
        snippet = text if len(text) <= 80 else f"{text[:79]}…"
        return f"text {snippet!r}"
    return "other"


async def log_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every inbound message with its sender so problems are traceable."""
    message = update.effective_message
    if message is None:
        return
    user = update.effective_user
    uid = user.id if user else "unknown"
    username = f"@{user.username}" if user and user.username else "-"
    trusted = bool(_whitelist(context).audio_filter.check_update(update))
    log.info(
        "Inbound from %s (%s, %s): %s",
        uid,
        username,
        "trusted" if trusted else "untrusted",
        describe_message(message),
    )


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


async def _run_pipeline(
    media_message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    status: Message | None,
    log_label: str,
    initiator: User | None,
) -> str:
    """Duplicate gate → download → SFTP upload → occ scan, returning the reply text.

    The destination filename is derived from the message metadata (file name,
    else artist/title tags), so duplicates are caught before any bytes are
    downloaded. If the destination already has a file with that name,
    `initiator` is asked (via inline buttons on `status`) whether to rename,
    overwrite, or cancel; no answer — or no `status` channel to ask on — means
    the file is not added. Stateless by construction — the temp directory is
    removed in `finally`, success or not. When `status` is given, live progress
    is edited into it; otherwise the transfer runs silently. Raises
    `UserFacingError` for failures whose message is safe to relay.
    """
    settings = _settings(context)
    destination = _destination(context)
    workdir = None
    try:
        filename = resolve_filename(media_message, settings)
        log.info("Upload started — %s: %s", log_label, filename)
        overwrite = False
        prompted = False
        if await destination.file_exists(filename):
            if status is None:
                log.info("Duplicate, no prompt channel — %s: %s not added", log_label, filename)
                return f"🚫 {filename} already exists — not added."
            prompted = True
            choice = await _ask_duplicate_choice(
                status, context, filename=filename, initiator=initiator
            )
            if choice is None:
                log.info("Duplicate prompt timed out — %s: %s not added", log_label, filename)
                return f"🚫 {filename} already exists — not added (no answer in time)."
            if choice is DuplicateChoice.cancel:
                log.info("Duplicate cancelled — %s: %s not added", log_label, filename)
                return "🚫 Cancelled."
            overwrite = choice is DuplicateChoice.overwrite
        if status is not None and prompted:
            await status.edit_text(f"⬇️ Receiving {filename} …")
        local = await download_media(media_message, settings, filename)
        workdir = local.parent
        reporter = None
        if status is not None:
            await status.edit_text(f"📤 Uploading {filename} …")
            reporter = UploadProgressReporter(status, filename)
        try:
            remote_name = await destination.upload(
                local, filename, progress=reporter, overwrite=overwrite
            )
        finally:
            if reporter is not None:
                await reporter.finish()
        reply = f"✅ Added {remote_name}"
        if settings.run_scan:
            if status is not None:
                await status.edit_text(f"🔍 Indexing {remote_name} …")
            scan = await destination.scan()
            if not scan.ok:
                reply = f"⚠️ Uploaded {remote_name}, but the library scan failed: {scan.detail}"
            elif scan.new_tracks is not None:
                reply = f"{reply} — {scan.new_tracks} track(s) indexed"
        log.info("Transfer complete — %s: %s", log_label, remote_name)
        return reply
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


async def _ask_duplicate_choice(
    status: Message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    filename: str,
    initiator: User | None,
) -> DuplicateChoice | None:
    """Ask what to do about a duplicate via inline buttons; None on timeout.

    The pipeline suspends on a future that `on_duplicate_choice` settles when an
    authorized user presses a button. Prompts for bot-initiated uploads (source
    bot relays) can be answered by any whitelisted user, since the initiating
    bot cannot press buttons itself.
    """
    settings = _settings(context)
    prompts = _duplicate_prompts(context)
    token, future = prompts.create(
        initiator_id=initiator.id if initiator is not None else None,
        initiator_is_bot=initiator.is_bot if initiator is not None else True,
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Upload anyway (rename)",
                    callback_data=build_callback_data(token, DuplicateChoice.rename),
                ),
                InlineKeyboardButton(
                    "Overwrite",
                    callback_data=build_callback_data(token, DuplicateChoice.overwrite),
                ),
            ],
            [
                InlineKeyboardButton(
                    "Cancel", callback_data=build_callback_data(token, DuplicateChoice.cancel)
                )
            ],
        ]
    )
    try:
        await status.edit_text(
            f"⚠️ {filename} already exists in the music library. What would you like to do?",
            reply_markup=keyboard,
        )
        return await asyncio.wait_for(future, timeout=settings.duplicate_check_timeout_secs)
    except TimeoutError:
        return None
    finally:
        prompts.discard(token)


async def on_duplicate_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Settle a pending duplicate prompt from an inline button press."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    parsed = parse_callback_data(query.data)
    if parsed is None:
        await query.answer()
        return
    token, choice = parsed
    user = query.from_user
    outcome = _duplicate_prompts(context).resolve(
        token,
        choice,
        user_id=user.id,
        is_whitelisted=_whitelist(context).is_allowed_user(user.id),
    )
    if outcome is ResolveOutcome.not_allowed:
        await query.answer("Only the requester can decide this upload.")
    elif outcome is ResolveOutcome.expired:
        await query.answer("This prompt has expired.")
    else:
        await query.answer()


async def _finish_upload(
    target: Message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    status: Message,
    log_label: str,
    initiator: User | None,
) -> None:
    """Upload `target`'s audio, editing live progress + the result into `status`."""
    try:
        reply = await _run_pipeline(
            target, context, status=status, log_label=log_label, initiator=initiator
        )
    except UserFacingError as exc:
        log.warning("Transfer failed — %s: %s", log_label, exc)
        reply = f"❌ {exc}"
    except Exception:
        log.exception("Transfer failed — %s", log_label)
        reply = "❌ Transfer failed — see the bot logs for details."
    with contextlib.suppress(Forbidden, BadRequest):
        await status.edit_text(reply)


def _mentions_bot(message: Message, username: str | None) -> bool:
    if not username:
        return False
    handle = f"@{username}".lower()
    mentions = {
        **message.parse_entities([MessageEntity.MENTION]),
        **message.parse_caption_entities([MessageEntity.MENTION]),
    }
    return any(text.lower() == handle for text in mentions.values())


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ack → run the upload pipeline → reply, for audio sent directly to the bot."""
    message = update.effective_message
    if message is None:
        return
    uid = update.effective_user.id if update.effective_user else "unknown"
    try:
        status = await message.reply_text("⬇️ Receiving…")
    except (Forbidden, BadRequest) as exc:
        log.warning("Can't post for user %s (%s) — skipping", uid, exc)
        return
    await _finish_upload(
        message,
        context,
        status=status,
        log_label=f"user {uid}",
        initiator=update.effective_user,
    )


def _guest_result(text: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=uuid.uuid4().hex,
        title="nc-music-bot",
        input_message_content=InputTextMessageContent(message_text=text),
    )


async def handle_guest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Guest-mode upload: a whitelisted user replies to a song and mentions the bot.

    Works in chats the bot is not a member of. Telegram allows exactly one reply
    per trigger (`answerGuestQuery`), so the whole pipeline runs before that single
    reply; live progress is best-effort DM'd to the caller's private chat with the
    bot when reachable.
    """
    gm = update.guest_message
    if gm is None:
        return
    caller = gm.guest_bot_caller_user
    if caller is None or not _whitelist(context).is_allowed_user(caller.id):
        cid = caller.id if caller else "unknown"
        log.warning("Rejected guest upload from non-whitelisted user %s", cid)
        await gm.answer_guest_query(
            _guest_result(
                f"Not authorized. Your Telegram ID is {cid} — "
                "it must be added to ALLOWED_USER_IDS before you can use this bot."
            )
        )
        return
    target = gm.reply_to_message
    if target is None or not is_supported_audio(target):
        log.info("Guest trigger from user %s without a supported audio reply", caller.id)
        await gm.answer_guest_query(_guest_result(GUEST_HINT_TEXT))
        return

    dm: Message | None = None
    try:
        dm = await context.bot.send_message(caller.id, "⬇️ Receiving…")
    except (Forbidden, BadRequest):
        dm = None
    try:
        reply = await _run_pipeline(
            target, context, status=dm, log_label=f"guest user {caller.id}", initiator=caller
        )
    except UserFacingError as exc:
        log.warning("Guest transfer failed — user %s: %s", caller.id, exc)
        reply = f"❌ {exc}"
    except Exception:
        log.exception("Guest transfer failed — user %s", caller.id)
        reply = "❌ Transfer failed — see the bot logs for details."
    await gm.answer_guest_query(_guest_result(reply))
    if dm is not None:
        with contextlib.suppress(Forbidden, BadRequest):
            await dm.edit_text(reply)


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


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a whitelisted user's non-audio message.

    If the message replies to a supported audio message and mentions the bot,
    that replied-to song is uploaded — this is the in-group equivalent of guest
    mode, for chats the bot is a member of. Otherwise (links, plain text, photos,
    stickers, …) the user gets a short hint so they aren't left in silence.
    """
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    uid = user.id
    target = message.reply_to_message
    if (
        target is not None
        and is_supported_audio(target)
        and _mentions_bot(message, context.bot.username)
    ):
        log.info("Reply-upload trigger from user %s", uid)
        where = f" in '{message.chat.title}'" if message.chat.title else ""
        try:
            await context.bot.send_message(
                uid,
                f"🎵 You mentioned me on a song{where} — adding it to the music library…",
            )
            status = await context.bot.send_message(uid, "⬇️ Receiving…")
        except (Forbidden, BadRequest) as exc:
            log.warning(
                "Can't DM user %s (%s) — they must start a private chat with the bot first",
                uid,
                exc,
            )
            return
        await _finish_upload(
            target, context, status=status, log_label=f"user {uid}", initiator=user
        )
        return
    log.info("Ignoring non-audio message from user %s", uid)
    with contextlib.suppress(Forbidden, BadRequest):
        await message.reply_text(
            "That doesn't look like an audio file. Reply to a song and mention me to add "
            "it, or send a song as music or a file (.mp3, .flac, .m4a, .ogg, …)."
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception while processing an update", exc_info=context.error)
