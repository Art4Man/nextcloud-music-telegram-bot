"""Stage destination: copy uploads into a local throwaway dir, no Tailscale/SSH.

Lets the bot run end-to-end on a developer laptop or a disposable deployment with
zero remote dependencies. The stage directory is wiped on every startup, so files
never accumulate across restarts.
"""

import asyncio
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .destination import CheckItem, ScanResult
from .errors import UserFacingError
from .naming import is_safe_remote_name, numbered_variant

log = logging.getLogger(__name__)


class LocalStageDestination:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._dir = settings.effective_stage_dir

    async def prepare(self) -> None:
        """Wipe and recreate the stage dir — the restart cleanup."""
        await asyncio.to_thread(self._reset_dir)
        log.info("Stage mode: serving uploads from %s (cleared on restart)", self._dir)

    def _reset_dir(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def upload(
        self, local: Path, filename: str, progress: Callable[[int, int], None] | None = None
    ) -> str:
        """Copy `local` into the stage dir; returns the (collision-safe) final name."""
        if not is_safe_remote_name(filename):
            raise UserFacingError(f"Refusing unsafe filename: {filename!r}")
        try:
            final = await asyncio.to_thread(self._copy_in, local, filename)
        except OSError as exc:
            raise UserFacingError(f"Stage copy failed: {exc}") from exc
        if progress is not None:
            size = local.stat().st_size
            progress(size, size)
        log.info("Staged %s -> %s", local.name, self._dir / final)
        return final

    def _copy_in(self, local: Path, filename: str) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        final = filename
        counter = 1
        while (self._dir / final).exists():
            final = numbered_variant(filename, counter)
            counter += 1
        target = self._dir / final
        part = target.with_name(f"{target.name}.part")
        shutil.copyfile(local, part)
        os.replace(part, target)
        return final

    async def scan(self) -> ScanResult:
        """No library to index in stage mode."""
        return ScanResult(ok=True, new_tracks=None, detail="stage")

    async def check(self) -> list[CheckItem]:
        """Doctor for `--check` and /status: stage mode + write access to the stage dir."""
        items = [CheckItem("stage mode", True, str(self._dir))]
        try:
            await asyncio.to_thread(self._write_probe)
            items.append(CheckItem("write access to STAGE_DIR", True, str(self._dir)))
        except OSError as exc:
            items.append(CheckItem("write access to STAGE_DIR", False, str(exc)))
        return items

    def _write_probe(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        probe = self._dir / ".nc-music-bot.write-test"
        probe.write_text("ok")
        probe.unlink()
