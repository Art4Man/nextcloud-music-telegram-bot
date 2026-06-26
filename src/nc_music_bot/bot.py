"""Application wiring: PTB app, optional local bot-api base URL, handler registration."""

import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from nc_music_bot.queue import QueueManager

from . import handlers
from .config import AppMode, Settings
from .destination import choose_destination
from .download import AUDIO_EXTENSIONS
from .whitelist import Whitelist

log = logging.getLogger(__name__)

_AUDIO_BY_EXTENSION: filters.BaseFilter = filters.Document.FileExtension(AUDIO_EXTENSIONS[0])
for _ext in AUDIO_EXTENSIONS[1:]:
    _AUDIO_BY_EXTENSION |= filters.Document.FileExtension(_ext)

AUDIO_MESSAGE = filters.AUDIO | filters.Document.AUDIO | _AUDIO_BY_EXTENSION


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("help", "How to use the bot"),
            BotCommand("myid", "Show your Telegram user ID"),
            BotCommand("status", "Check the connection to the music server"),
        ]
    )
    await app.bot_data["destination"].prepare()


def build_application(settings: Settings) -> Application:
    builder = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .connect_timeout(30)
        .read_timeout(120)
        .write_timeout(120)
        .post_init(_post_init)
    )
    if settings.telegram_api_base_url:
        base = settings.telegram_api_base_url
        # local_mode: the self-hosted bot-api returns absolute paths on its own
        # disk, which we can read directly via the shared volume.
        builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot").local_mode(True)
        log.info("Using self-hosted telegram-bot-api at %s (2 GB downloads)", base)

    app = builder.build()
    whitelist = Whitelist(settings)
    app.bot_data["settings"] = settings
    app.bot_data["destination"] = NextcloudDestination(settings)
    app.bot_data["queue_manager"] = QueueManager(handlers.process_audio_job,)

    trusted = whitelist.audio_filter
    app.add_handler(MessageHandler(filters.ALL, handlers.log_inbound), group=-1)
    app.add_handler(CommandHandler(["start", "help"], handlers.cmd_help))
    app.add_handler(CommandHandler("myid", handlers.cmd_myid))
    app.add_handler(CommandHandler("status", handlers.cmd_status, filters=whitelist.users))
    app.add_handler(CommandHandler("whitelist", handlers.cmd_whitelist))
    app.add_handler(CommandHandler("allow", handlers.cmd_allow))
    app.add_handler(CommandHandler("deny", handlers.cmd_deny))
    app.add_handler(CommandHandler("addbot", handlers.cmd_addbot))
    app.add_handler(CommandHandler("rmbot", handlers.cmd_rmbot))
    app.add_handler(MessageHandler(AUDIO_MESSAGE & trusted, handlers.handle_audio))
    app.add_handler(MessageHandler(AUDIO_MESSAGE & ~trusted, handlers.handle_unauthorized))
    app.add_handler(
        MessageHandler(trusted & ~AUDIO_MESSAGE & ~filters.COMMAND, handlers.handle_unsupported)
    )
    app.add_handler(MessageHandler(filters.UpdateType.GUEST_MESSAGE, handlers.handle_guest))
    app.add_error_handler(handlers.on_error)
    return app



def run_bot(settings: Settings) -> None:
    app = build_application(settings)
    if settings.app_mode is AppMode.stage:
        log.info("Relay ready (stage): Telegram -> %s", settings.effective_stage_dir)
    else:
        log.info(
            "Relay ready: Telegram -> %s@%s:%s%s",
            settings.dest_ssh_user,
            settings.dest_host,
            settings.dest_ssh_port,
            settings.dest_path,
        )
    app.run_polling(allowed_updates=Update.ALL_TYPES)
