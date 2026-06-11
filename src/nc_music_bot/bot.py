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

from . import handlers
from .auth import allowed_users_filter
from .config import Settings
from .destination import NextcloudDestination

log = logging.getLogger(__name__)

AUDIO_MESSAGE = filters.AUDIO | filters.Document.AUDIO


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("help", "How to use the bot"),
            BotCommand("myid", "Show your Telegram user ID"),
            BotCommand("status", "Check the connection to the music server"),
        ]
    )


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
    app.bot_data["settings"] = settings
    app.bot_data["destination"] = NextcloudDestination(settings)

    allowed = allowed_users_filter(settings)
    app.add_handler(CommandHandler(["start", "help"], handlers.cmd_help))
    app.add_handler(CommandHandler("myid", handlers.cmd_myid))
    app.add_handler(CommandHandler("status", handlers.cmd_status, filters=allowed))
    app.add_handler(MessageHandler(AUDIO_MESSAGE & allowed, handlers.handle_audio))
    app.add_handler(MessageHandler(AUDIO_MESSAGE & ~allowed, handlers.handle_unauthorized))
    app.add_error_handler(handlers.on_error)
    return app


def run_bot(settings: Settings) -> None:
    app = build_application(settings)
    log.info(
        "Relay ready: Telegram -> %s@%s:%s%s",
        settings.dest_ssh_user,
        settings.dest_host,
        settings.dest_ssh_port,
        settings.dest_path,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)
