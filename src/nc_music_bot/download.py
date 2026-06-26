"""Fetching the audio payload from Telegram into a per-file temp directory."""

import logging
import uuid
from pathlib import Path

from telegram import Audio, Document, Message

from .config import CLOUD_API_MAX_MB, MB, Settings
from .errors import UserFacingError
from .naming import build_filename

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS: tuple[str, ...] = (
    "mp3",
    "flac",
    "m4a",
    "ogg",
    "opus",
    "aac",
    "wav",
    "wma",
    "alac",
    "aiff",
    "mka",
)


def is_supported_audio(message: Message) -> bool:
    """Whether a message carries audio this bot will accept.

    Mirrors the registration-time `AUDIO_MESSAGE` filter so the guest handler can
    test a replied-to message, which never passes through that filter itself.
    """
    if message.audio is not None:
        return True
    doc = message.document
    if doc is None:
        return False
    if doc.mime_type and doc.mime_type.startswith("audio/"):
        return True
    name = doc.file_name or ""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension in AUDIO_EXTENSIONS


def pick_media(message: Message) -> Audio | Document:
    media = message.audio or message.document
    if media is None:
        raise UserFacingError("Send an audio file — as music or as a document.")
    return media


def check_size(media: Audio | Document, settings: Settings) -> None:
    size = media.file_size or 0
    if size > settings.max_file_bytes:
        raise UserFacingError(
            f"File is {size / MB:.0f} MB — over the {settings.max_file_mb} MB limit."
        )
    if not settings.telegram_api_base_url and size > CLOUD_API_MAX_MB * MB:
        raise UserFacingError(
            f"File is {size / MB:.0f} MB, but without a self-hosted telegram-bot-api the "
            f"standard Bot API caps bot downloads at {CLOUD_API_MAX_MB} MB. "
            "See README → “Large files (up to 2 GB)”."
        )


async def download_media(message: Message, settings: Settings) -> tuple[Path, str]:
    """Download the message's audio. Returns (local_path, filename).

    The file lands in a unique directory under TEMP_DIR; the caller owns
    deleting `local_path.parent` once the transfer is finished.
    """
    media = pick_media(message)
    check_size(media, settings)
    performer = media.performer if isinstance(media, Audio) else None
    title = media.title if isinstance(media, Audio) else None
    filename = build_filename(
        file_name=media.file_name,
        performer=performer,
        title=title,
        mime_type=media.mime_type,
        unique_id=media.file_unique_id,
    )
    workdir = settings.temp_dir / uuid.uuid4().hex
    workdir.mkdir(parents=True, exist_ok=True)
    local = workdir / filename
    tg_file = await media.get_file()
    await tg_file.download_to_drive(custom_path=local)
    log.info("Downloaded %s (%s bytes) from Telegram", filename, media.file_size)
    return local, filename
