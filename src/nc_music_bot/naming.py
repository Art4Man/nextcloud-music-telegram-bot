"""Filename handling: sanitization against path tricks + names for unnamed audio.

Pure functions — everything here is unit-tested without I/O.
"""

import mimetypes
import re

_MAX_NAME_BYTES = 200
_FALLBACK_STEM = "audio"
_WHITESPACE = re.compile(r"\s+")

# Audio MIME types Telegram commonly sends, where mimetypes guesses badly or not at all.
_EXT_BY_MIME = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def sanitize_filename(raw: str) -> str:
    """Reduce an attacker-controlled name to a single safe path component."""
    # Keep only the last path component, whichever separator style the sender used.
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _WHITESPACE.sub(" ", name).strip()
    # No hidden files — also collapses "." / ".." to nothing.
    name = name.lstrip(".")
    stem, ext = _split_ext(name)
    if not stem:
        stem = _FALLBACK_STEM
    return _truncate(stem, ext)


def build_filename(
    file_name: str | None,
    performer: str | None,
    title: str | None,
    mime_type: str | None,
    unique_id: str,
) -> str:
    """Best filename for a Telegram audio: the sent name, else tags, else the file ID."""
    if file_name:
        return sanitize_filename(file_name)
    ext = ""
    if mime_type:
        mime = mime_type.lower()
        ext = _EXT_BY_MIME.get(mime) or mimetypes.guess_extension(mime) or ""
    stem = " - ".join(part.strip() for part in (performer, title) if part and part.strip())
    if not stem:
        stem = f"{_FALLBACK_STEM}_{unique_id}"
    return sanitize_filename(stem + ext)


def numbered_variant(filename: str, n: int) -> str:
    """`song.mp3` → `song (1).mp3`, for collision-safe remote naming."""
    stem, ext = _split_ext(filename)
    return f"{stem} ({n}){ext}"


def is_safe_remote_name(name: str) -> bool:
    """Final gate before joining onto DEST_PATH: one plain, non-hidden component."""
    return (
        bool(name)
        and "/" not in name
        and "\\" not in name
        and not name.startswith(".")
        and name not in {".", ".."}
    )


def _split_ext(name: str) -> tuple[str, str]:
    root, dot, ext = name.rpartition(".")
    if not dot or not root:
        return name, ""
    return root, f".{ext}"


def _truncate(stem: str, ext: str) -> str:
    """Keep the name under _MAX_NAME_BYTES (UTF-8), sacrificing the stem, never the extension."""
    while len(stem) > 1 and len(f"{stem}{ext}".encode()) > _MAX_NAME_BYTES:
        stem = stem[:-1].rstrip()
    return f"{stem}{ext}"
