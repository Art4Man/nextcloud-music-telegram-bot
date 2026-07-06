"""The destination side: SFTP upload over Tailscale + occ scans, via asyncssh.

One short-lived SSH connection per operation — uploads are rare, and it keeps
the bot fully stateless across destination reboots.
"""

import logging
import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import asyncssh

from .config import AppMode, Settings
from .errors import UserFacingError
from .naming import is_safe_remote_name, numbered_variant

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 15  # seconds; tailnet peers answer fast or not at all
_SCAN_TIMEOUT = 600  # occ scans are scoped to the music path, but allow slow disks

# Phrases the Music app / files:scan print about how much was indexed.
_TRACK_COUNT_PATTERNS = (
    re.compile(r"found\s+(\d+)\s+new", re.IGNORECASE),
    re.compile(r"(\d+)\s+music files?\s+(?:were|was)\s+scanned", re.IGNORECASE),
    re.compile(r"scanned\s+(\d+)", re.IGNORECASE),
)


@dataclass
class ScanResult:
    ok: bool
    new_tracks: int | None
    detail: str = ""


@dataclass
class CheckItem:
    label: str
    ok: bool
    detail: str = ""


class Destination(Protocol):
    """The upload target: production Nextcloud over SFTP, or a local stage dir."""

    async def prepare(self) -> None: ...

    async def file_exists(self, filename: str) -> bool: ...

    async def upload(
        self,
        local: Path,
        filename: str,
        progress: Callable[[int, int], None] | None = None,
        *,
        overwrite: bool = False,
    ) -> str: ...

    async def scan(self) -> ScanResult: ...

    async def check(self) -> list[CheckItem]: ...


def choose_destination(settings: Settings) -> Destination:
    """Pick the destination implementation for the configured `APP_MODE`."""
    if settings.app_mode is AppMode.stage:
        from .stage import LocalStageDestination

        return LocalStageDestination(settings)
    return NextcloudDestination(settings)


class NextcloudDestination:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def prepare(self) -> None:
        """Nothing to set up remotely; the destination is reached on demand."""

    async def file_exists(self, filename: str) -> bool:
        """True if `filename` is already present in DEST_PATH.

        Checked before an upload so the user can pick rename/overwrite/cancel.
        The check and the upload are separate SFTP operations, so a concurrent
        upload of the same name can still slip in between (known limitation).
        """
        if not is_safe_remote_name(filename):
            raise UserFacingError(f"Refusing unsafe filename: {filename!r}")
        s = self._s
        conn = await self._connect()
        try:
            sftp = await conn.start_sftp_client()
            return await sftp.exists(posixpath.join(s.dest_path, filename))
        except (OSError, asyncssh.Error) as exc:
            raise UserFacingError(f"Duplicate check on {s.dest_host} failed: {exc}") from exc
        finally:
            conn.close()
            await conn.wait_closed()

    async def upload(
        self,
        local: Path,
        filename: str,
        progress: Callable[[int, int], None] | None = None,
        *,
        overwrite: bool = False,
    ) -> str:
        """Upload `local` into DEST_PATH; returns the (collision-safe) remote name.

        `progress`, if given, is called with (bytes copied, total bytes) as blocks
        land. With `overwrite`, an existing file of the same name is replaced —
        it is removed only after the new content has fully arrived as `.part`,
        so the old file stays intact until the last moment.
        """
        if not is_safe_remote_name(filename):
            raise UserFacingError(f"Refusing unsafe filename: {filename!r}")
        s = self._s
        conn = await self._connect()
        try:
            sftp = await conn.start_sftp_client()
            await sftp.makedirs(s.dest_path, exist_ok=True)
            final = filename
            if not overwrite:
                counter = 1
                while await sftp.exists(posixpath.join(s.dest_path, final)):
                    final = numbered_variant(filename, counter)
                    counter += 1
            target = posixpath.join(s.dest_path, final)
            # Stage under a .part name so a half-written file can never be scanned in.
            handler = _adapt_progress(progress) if progress is not None else None
            await sftp.put(str(local), f"{target}.part", progress_handler=handler)
            if overwrite and await sftp.exists(target):
                await sftp.remove(target)
            await sftp.rename(f"{target}.part", target)
            log.info("Uploaded %s -> %s", local.name, target)
        except (OSError, asyncssh.Error) as exc:
            raise UserFacingError(f"Upload to {s.dest_host} failed: {exc}") from exc
        finally:
            conn.close()
            await conn.wait_closed()
        return final

    async def scan(self) -> ScanResult:
        """Run `occ files:scan` + `occ music:scan` so the new file gets indexed."""
        s = self._s
        occ = s.nextcloud_occ
        files_cmd = f"{occ} files:scan --path={shlex.quote(s.nextcloud_scan_path or '')}"
        music_cmd = f"{occ} music:scan {shlex.quote(s.nextcloud_user)}"
        conn = await self._connect()
        try:
            files_res = await conn.run(files_cmd, check=False, timeout=_SCAN_TIMEOUT)
            music_res = await conn.run(music_cmd, check=False, timeout=_SCAN_TIMEOUT)
        except (OSError, asyncssh.Error) as exc:
            return ScanResult(ok=False, new_tracks=None, detail=f"scan failed: {exc}")
        finally:
            conn.close()
            await conn.wait_closed()
        ok = files_res.exit_status == 0 and music_res.exit_status == 0
        detail = ""
        if not ok:
            detail = "; ".join(
                f"{name} exited {res.exit_status}: {_tail(str(res.stderr or res.stdout or ''))}"
                for name, res in (("files:scan", files_res), ("music:scan", music_res))
                if res.exit_status != 0
            )
            log.error("Scan failed: %s", detail)
        return ScanResult(
            ok=ok,
            new_tracks=parse_track_count(str(music_res.stdout or "")),
            detail=detail,
        )

    async def check(self) -> list[CheckItem]:
        """Doctor for `--check` and /status: connectivity, occ presence, write access."""
        s = self._s
        items: list[CheckItem] = []
        try:
            conn = await self._connect()
        except UserFacingError as exc:
            items.append(CheckItem("SSH connection", False, str(exc)))
            return items
        try:
            items.append(
                CheckItem(
                    "SSH connection", True, f"{s.dest_ssh_user}@{s.dest_host}:{s.dest_ssh_port}"
                )
            )
            if s.run_scan:
                occ_bin = s.nextcloud_occ.split()[0]
                res = await conn.run(f"command -v {shlex.quote(occ_bin)}", check=False)
                found = res.exit_status == 0
                items.append(
                    CheckItem(
                        f"occ command ({occ_bin})",
                        found,
                        str(res.stdout or "").strip() if found else "not found on destination",
                    )
                )
            try:
                sftp = await conn.start_sftp_client()
                await sftp.makedirs(s.dest_path, exist_ok=True)
                probe = posixpath.join(s.dest_path, ".nc-music-bot.write-test")
                fh = await sftp.open(probe, "w")
                await fh.write("ok")
                await fh.close()
                await sftp.remove(probe)
                items.append(CheckItem("write access to DEST_PATH", True, s.dest_path))
            except (OSError, asyncssh.Error) as exc:
                items.append(CheckItem("write access to DEST_PATH", False, str(exc)))
        finally:
            conn.close()
            await conn.wait_closed()
        return items

    async def _connect(self) -> asyncssh.SSHClientConnection:
        s = self._s
        options: dict[str, Any] = {
            "host": s.dest_host,
            "port": s.dest_ssh_port,
            "username": s.dest_ssh_user,
            "known_hosts": str(await self._known_hosts_file()),
            "connect_timeout": _CONNECT_TIMEOUT,
        }
        if s.dest_ssh_key_path:
            options["client_keys"] = [str(s.dest_ssh_key_path)]
        else:
            options["password"] = s.dest_ssh_password
        try:
            return await asyncssh.connect(**options)
        except (OSError, asyncssh.Error) as exc:
            raise UserFacingError(
                f"Can't reach {s.dest_host}:{s.dest_ssh_port} over the tailnet: {exc}"
            ) from exc

    async def _known_hosts_file(self) -> Path:
        """Host-key pinning: use DEST_KNOWN_HOSTS if set, else pin on first connect (TOFU)."""
        s = self._s
        path = s.dest_known_hosts or _default_known_hosts_path()
        if not path.exists():
            try:
                key = await asyncssh.get_server_host_key(s.dest_host, s.dest_ssh_port)
            except (OSError, asyncssh.Error) as exc:
                raise UserFacingError(f"Can't fetch the host key of {s.dest_host}: {exc}") from exc
            if key is None:
                raise UserFacingError(f"{s.dest_host} offered no SSH host key")
            pattern = s.dest_host if s.dest_ssh_port == 22 else f"[{s.dest_host}]:{s.dest_ssh_port}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{pattern} {key.export_public_key().decode().strip()}\n")
            path.chmod(0o600)
            log.warning(
                "Pinned host key for %s on first connect (%s) -> %s",
                s.dest_host,
                key.get_fingerprint(),
                path,
            )
        return path


def _adapt_progress(
    progress: Callable[[int, int], None],
) -> Callable[[bytes, bytes, int, int], None]:
    """Shrink asyncssh's (src, dst, copied, total) handler down to (copied, total)."""

    def handler(_src: bytes, _dst: bytes, copied: int, total: int) -> None:
        progress(copied, total)

    return handler


def parse_track_count(scan_output: str) -> int | None:
    """Best-effort count of indexed tracks from occ output; None if unrecognized."""
    for pattern in _TRACK_COUNT_PATTERNS:
        match = pattern.search(scan_output)
        if match:
            return int(match.group(1))
    return None


def _default_known_hosts_path() -> Path:
    return Path.home() / ".config" / "nc-music-bot" / "known_hosts"


def _tail(text: str, lines: int = 3) -> str:
    return " / ".join(line for line in text.strip().splitlines()[-lines:] if line.strip())
