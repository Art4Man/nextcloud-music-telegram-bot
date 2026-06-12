import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from telegram.error import TelegramError

log = logging.getLogger(__name__)

_EDIT_INTERVAL = 1.0
_BAR_SEGMENTS = 10


class EditableMessage(Protocol):
    """The one slice of `telegram.Message` the reporter needs (and tests can fake)."""

    async def edit_text(self, text: str) -> object: ...


def format_upload_progress(filename: str, copied: int, total: int) -> str:
    """Render the two-line status text: filename, then bar / percent / MB."""
    if total <= 0:
        return f"📤 Uploading {filename} …"
    fraction = min(copied / total, 1.0)
    filled = int(fraction * _BAR_SEGMENTS)
    bar = "▓" * filled + "░" * (_BAR_SEGMENTS - filled)
    mb = 1024 * 1024
    return (
        f"📤 Uploading {filename} …\n"
        f"{bar} {int(fraction * 100)}% ({copied / mb:.1f} / {total / mb:.1f} MB)"
    )


class UploadProgressReporter:
    """Throttled bridge from asyncssh's sync progress callback to async Telegram edits.

    Construct it right after the initial "📤 Uploading…" edit — the throttle clock
    starts at construction, so the first progress edit lands a full interval later.
    Call `finish()` once the upload is over (success or not) so a still-in-flight
    progress edit can't overwrite whatever status comes next.
    """

    def __init__(
        self,
        status: EditableMessage,
        filename: str,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._status = status
        self._filename = filename
        self._now = now
        self._last_time = now()
        self._last_pct = -1
        self._task: asyncio.Task[None] | None = None

    def __call__(self, copied: int, total: int) -> None:
        if total <= 0:
            return
        if self._task is not None and not self._task.done():
            return
        pct = int(min(copied / total, 1.0) * 100)
        now = self._now()
        if now - self._last_time < _EDIT_INTERVAL or pct == self._last_pct:
            return
        self._last_time = now
        self._last_pct = pct
        self._task = asyncio.create_task(self._edit(copied, total))

    async def _edit(self, copied: int, total: int) -> None:
        try:
            await self._status.edit_text(format_upload_progress(self._filename, copied, total))
        except TelegramError as exc:
            log.debug("Progress edit skipped: %s", exc)

    async def finish(self) -> None:
        if self._task is None:
            return
        try:
            await self._task
        except Exception:
            log.debug("Pending progress edit failed", exc_info=True)
        self._task = None
