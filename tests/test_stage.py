from pathlib import Path

import pytest

from nc_music_bot.config import AppMode
from nc_music_bot.destination import NextcloudDestination, choose_destination
from nc_music_bot.errors import UserFacingError
from nc_music_bot.stage import LocalStageDestination

from .conftest import MakeSettings


def _stage(make_settings: MakeSettings, tmp_path: Path) -> LocalStageDestination:
    settings = make_settings(app_mode="stage", stage_dir=tmp_path / "stage")
    return LocalStageDestination(settings)


def test_choose_destination_picks_by_mode(make_settings: MakeSettings, tmp_path: Path) -> None:
    stage = choose_destination(make_settings(app_mode="stage", stage_dir=tmp_path / "stage"))
    prod = choose_destination(make_settings())
    assert isinstance(stage, LocalStageDestination)
    assert isinstance(prod, NextcloudDestination)


async def test_prepare_wipes_existing_files(make_settings: MakeSettings, tmp_path: Path) -> None:
    dest = _stage(make_settings, tmp_path)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "leftover.mp3").write_text("old")
    await dest.prepare()
    assert stage_dir.exists()
    assert list(stage_dir.iterdir()) == []


async def test_upload_copies_file_and_reports_progress(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    dest = _stage(make_settings, tmp_path)
    await dest.prepare()
    src = tmp_path / "song.mp3"
    src.write_bytes(b"audio-bytes")
    seen: list[tuple[int, int]] = []

    def on_progress(copied: int, total: int) -> None:
        seen.append((copied, total))

    final = await dest.upload(src, "song.mp3", progress=on_progress)

    assert final == "song.mp3"
    staged = tmp_path / "stage" / "song.mp3"
    assert staged.read_bytes() == b"audio-bytes"
    assert not (tmp_path / "stage" / "song.mp3.part").exists()
    assert seen == [(len(b"audio-bytes"), len(b"audio-bytes"))]


async def test_upload_is_collision_safe(make_settings: MakeSettings, tmp_path: Path) -> None:
    dest = _stage(make_settings, tmp_path)
    await dest.prepare()
    src = tmp_path / "song.mp3"
    src.write_bytes(b"x")

    first = await dest.upload(src, "song.mp3")
    second = await dest.upload(src, "song.mp3")

    assert first == "song.mp3"
    assert second == "song (1).mp3"
    assert (tmp_path / "stage" / "song (1).mp3").exists()


async def test_file_exists_sees_staged_files(make_settings: MakeSettings, tmp_path: Path) -> None:
    dest = _stage(make_settings, tmp_path)
    await dest.prepare()
    src = tmp_path / "song.mp3"
    src.write_bytes(b"x")
    await dest.upload(src, "song.mp3")

    assert await dest.file_exists("song.mp3")
    assert not await dest.file_exists("other.mp3")


async def test_upload_overwrite_replaces_existing(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    dest = _stage(make_settings, tmp_path)
    await dest.prepare()
    first = tmp_path / "old.mp3"
    first.write_bytes(b"old")
    second = tmp_path / "new.mp3"
    second.write_bytes(b"new")
    await dest.upload(first, "song.mp3")

    final = await dest.upload(second, "song.mp3", overwrite=True)

    assert final == "song.mp3"
    assert (tmp_path / "stage" / "song.mp3").read_bytes() == b"new"
    assert not (tmp_path / "stage" / "song (1).mp3").exists()


async def test_upload_rejects_unsafe_name(make_settings: MakeSettings, tmp_path: Path) -> None:
    dest = _stage(make_settings, tmp_path)
    await dest.prepare()
    src = tmp_path / "song.mp3"
    src.write_bytes(b"x")
    with pytest.raises(UserFacingError):
        await dest.upload(src, "../escape.mp3")


async def test_scan_is_a_noop(make_settings: MakeSettings, tmp_path: Path) -> None:
    dest = _stage(make_settings, tmp_path)
    result = await dest.scan()
    assert result.ok
    assert result.new_tracks is None


async def test_check_reports_stage_and_writable(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    dest = _stage(make_settings, tmp_path)
    items = await dest.check()
    assert all(item.ok for item in items)
    labels = [item.label for item in items]
    assert "stage mode" in labels
    assert any("write access" in label for label in labels)


def test_settings_app_mode_enum(make_settings: MakeSettings, tmp_path: Path) -> None:
    settings = make_settings(app_mode="stage", stage_dir=tmp_path / "stage")
    assert settings.app_mode is AppMode.stage
