from collections.abc import Callable
from pathlib import Path
from typing import Any

import asyncssh
import pytest

from nc_music_bot.destination import NextcloudDestination, parse_track_count
from nc_music_bot.errors import UserFacingError

from .conftest import MakeSettings


class FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_status: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status


class FakeFile:
    async def write(self, data: str) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeSFTP:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = set(existing or ())
        self.puts: list[tuple[str, str]] = []
        self.renames: list[tuple[str, str]] = []
        self.removed: list[str] = []

    async def makedirs(self, path: str, exist_ok: bool = False) -> None:
        return None

    async def exists(self, path: str) -> bool:
        return path in self.existing

    async def put(
        self,
        local: str,
        remote: str,
        progress_handler: Callable[[bytes, bytes, int, int], None] | None = None,
    ) -> None:
        if progress_handler is not None:
            progress_handler(local.encode(), remote.encode(), 512, 1024)
            progress_handler(local.encode(), remote.encode(), 1024, 1024)
        self.puts.append((local, remote))
        self.existing.add(remote)

    async def rename(self, src: str, dst: str) -> None:
        self.renames.append((src, dst))
        self.existing.discard(src)
        self.existing.add(dst)

    async def open(self, path: str, mode: str) -> FakeFile:
        self.existing.add(path)
        return FakeFile()

    async def remove(self, path: str) -> None:
        self.existing.discard(path)
        self.removed.append(path)


class FakeConn:
    def __init__(
        self,
        sftp: FakeSFTP | None = None,
        results: dict[str, FakeResult] | None = None,
    ) -> None:
        self.sftp = sftp or FakeSFTP()
        self.results = results or {}
        self.commands: list[str] = []
        self.closed = False

    async def start_sftp_client(self) -> FakeSFTP:
        return self.sftp

    async def run(
        self, command: str, check: bool = False, timeout: int | None = None
    ) -> FakeResult:
        self.commands.append(command)
        for fragment, result in self.results.items():
            if fragment in command:
                return result
        return FakeResult()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


PatchConnect = Callable[[FakeConn], None]


@pytest.fixture
def patch_connect(monkeypatch: pytest.MonkeyPatch) -> PatchConnect:
    def _patch(conn: FakeConn) -> None:
        async def fake_connect(**kwargs: Any) -> FakeConn:
            return conn

        monkeypatch.setattr(asyncssh, "connect", fake_connect)

    return _patch


async def test_upload_puts_part_file_then_renames(
    make_settings: MakeSettings, patch_connect: PatchConnect, tmp_path: Path
) -> None:
    conn = FakeConn()
    patch_connect(conn)
    local = tmp_path / "song.mp3"
    local.write_bytes(b"x")

    final = await NextcloudDestination(make_settings()).upload(local, "song.mp3")

    assert final == "song.mp3"
    assert conn.sftp.puts == [(str(local), "/srv/music/song.mp3.part")]
    assert conn.sftp.renames == [("/srv/music/song.mp3.part", "/srv/music/song.mp3")]
    assert conn.closed


async def test_upload_avoids_collisions(
    make_settings: MakeSettings, patch_connect: PatchConnect, tmp_path: Path
) -> None:
    conn = FakeConn(sftp=FakeSFTP({"/srv/music/song.mp3", "/srv/music/song (1).mp3"}))
    patch_connect(conn)
    local = tmp_path / "song.mp3"
    local.write_bytes(b"x")

    final = await NextcloudDestination(make_settings()).upload(local, "song.mp3")

    assert final == "song (2).mp3"


async def test_file_exists_checks_dest_path(
    make_settings: MakeSettings, patch_connect: PatchConnect
) -> None:
    conn = FakeConn(sftp=FakeSFTP({"/srv/music/song.mp3"}))
    patch_connect(conn)
    dest = NextcloudDestination(make_settings())

    assert await dest.file_exists("song.mp3")
    assert not await dest.file_exists("other.mp3")
    assert conn.closed


async def test_file_exists_rejects_unsafe_names(make_settings: MakeSettings) -> None:
    with pytest.raises(UserFacingError, match="unsafe filename"):
        await NextcloudDestination(make_settings()).file_exists("../evil.mp3")


async def test_upload_overwrite_replaces_existing(
    make_settings: MakeSettings, patch_connect: PatchConnect, tmp_path: Path
) -> None:
    conn = FakeConn(sftp=FakeSFTP({"/srv/music/song.mp3"}))
    patch_connect(conn)
    local = tmp_path / "song.mp3"
    local.write_bytes(b"x")

    final = await NextcloudDestination(make_settings()).upload(local, "song.mp3", overwrite=True)

    assert final == "song.mp3"
    assert conn.sftp.puts == [(str(local), "/srv/music/song.mp3.part")]
    assert conn.sftp.removed == ["/srv/music/song.mp3"]
    assert conn.sftp.renames == [("/srv/music/song.mp3.part", "/srv/music/song.mp3")]


async def test_upload_overwrite_without_existing_file(
    make_settings: MakeSettings, patch_connect: PatchConnect, tmp_path: Path
) -> None:
    conn = FakeConn()
    patch_connect(conn)
    local = tmp_path / "song.mp3"
    local.write_bytes(b"x")

    final = await NextcloudDestination(make_settings()).upload(local, "song.mp3", overwrite=True)

    assert final == "song.mp3"
    assert conn.sftp.removed == []
    assert conn.sftp.renames == [("/srv/music/song.mp3.part", "/srv/music/song.mp3")]


async def test_upload_forwards_progress(
    make_settings: MakeSettings, patch_connect: PatchConnect, tmp_path: Path
) -> None:
    conn = FakeConn()
    patch_connect(conn)
    local = tmp_path / "song.mp3"
    local.write_bytes(b"x")
    seen: list[tuple[int, int]] = []

    await NextcloudDestination(make_settings()).upload(
        local, "song.mp3", progress=lambda copied, total: seen.append((copied, total))
    )

    assert seen == [(512, 1024), (1024, 1024)]


async def test_upload_rejects_unsafe_names(make_settings: MakeSettings, tmp_path: Path) -> None:
    with pytest.raises(UserFacingError, match="unsafe filename"):
        await NextcloudDestination(make_settings()).upload(tmp_path / "x", "../evil.mp3")


async def test_connection_failure_is_user_facing(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def boom(**kwargs: Any) -> FakeConn:
        raise OSError("no route to host")

    monkeypatch.setattr(asyncssh, "connect", boom)
    local = tmp_path / "song.mp3"
    local.write_bytes(b"x")

    with pytest.raises(UserFacingError, match="Can't reach"):
        await NextcloudDestination(make_settings()).upload(local, "song.mp3")


async def test_scan_runs_both_occ_commands(
    make_settings: MakeSettings, patch_connect: PatchConnect
) -> None:
    conn = FakeConn(results={"music:scan": FakeResult(stdout="Found 2 new music files\n")})
    patch_connect(conn)

    result = await NextcloudDestination(make_settings()).scan()

    assert conn.commands == [
        "nextcloud.occ files:scan --path=admin/files/Music",
        "nextcloud.occ music:scan admin",
    ]
    assert result.ok
    assert result.new_tracks == 2


async def test_scan_failure_reported_with_detail(
    make_settings: MakeSettings, patch_connect: PatchConnect
) -> None:
    conn = FakeConn(results={"music:scan": FakeResult(stderr="boom", exit_status=1)})
    patch_connect(conn)

    result = await NextcloudDestination(make_settings()).scan()

    assert not result.ok
    assert "music:scan" in result.detail
    assert "boom" in result.detail


async def test_check_reports_green_path(
    make_settings: MakeSettings, patch_connect: PatchConnect
) -> None:
    conn = FakeConn(results={"command -v": FakeResult(stdout="/usr/bin/nextcloud.occ\n")})
    patch_connect(conn)

    items = await NextcloudDestination(make_settings()).check()

    assert [item.ok for item in items] == [True, True, True]
    assert conn.sftp.removed == ["/srv/music/.nc-music-bot.write-test"]


async def test_check_reports_unreachable_destination(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**kwargs: Any) -> FakeConn:
        raise OSError("no route to host")

    monkeypatch.setattr(asyncssh, "connect", boom)

    items = await NextcloudDestination(make_settings()).check()

    assert len(items) == 1
    assert not items[0].ok


def test_parse_track_count_variants() -> None:
    assert parse_track_count("Found 2 new music files") == 2
    assert parse_track_count("3 music files were scanned") == 3
    assert parse_track_count("Scanned 7 files in total") == 7
    assert parse_track_count("nothing recognizable") is None
