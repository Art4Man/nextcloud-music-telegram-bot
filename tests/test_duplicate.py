import asyncio
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardMarkup, Message, Update, User
from telegram.ext import ContextTypes

from nc_music_bot import handlers
from nc_music_bot.config import Settings
from nc_music_bot.destination import ScanResult
from nc_music_bot.duplicate import (
    DuplicateChoice,
    DuplicatePrompts,
    ResolveOutcome,
    build_callback_data,
    parse_callback_data,
)
from nc_music_bot.whitelist import Whitelist

from .conftest import MakeSettings


def test_callback_data_round_trip() -> None:
    data = build_callback_data("abc123", DuplicateChoice.overwrite)
    assert parse_callback_data(data) == ("abc123", DuplicateChoice.overwrite)


def test_parse_rejects_malformed_data() -> None:
    assert parse_callback_data("dup:abc") is None
    assert parse_callback_data("other:abc:rename") is None
    assert parse_callback_data("dup:abc:explode") is None
    assert parse_callback_data("") is None


async def test_initiator_resolves_own_prompt() -> None:
    prompts = DuplicatePrompts()
    token, future = prompts.create(initiator_id=123, initiator_is_bot=False)

    outcome = prompts.resolve(token, DuplicateChoice.rename, user_id=123, is_whitelisted=True)

    assert outcome is ResolveOutcome.resolved
    assert await future is DuplicateChoice.rename


async def test_other_users_cannot_decide_a_human_prompt() -> None:
    prompts = DuplicatePrompts()
    token, future = prompts.create(initiator_id=123, initiator_is_bot=False)

    outcome = prompts.resolve(token, DuplicateChoice.cancel, user_id=456, is_whitelisted=True)

    assert outcome is ResolveOutcome.not_allowed
    assert not future.done()


async def test_whitelisted_user_decides_bot_initiated_prompt() -> None:
    prompts = DuplicatePrompts()
    token, future = prompts.create(initiator_id=999, initiator_is_bot=True)

    stranger = prompts.resolve(token, DuplicateChoice.overwrite, user_id=1, is_whitelisted=False)
    member = prompts.resolve(token, DuplicateChoice.overwrite, user_id=123, is_whitelisted=True)

    assert stranger is ResolveOutcome.not_allowed
    assert member is ResolveOutcome.resolved
    assert await future is DuplicateChoice.overwrite


async def test_unknown_or_settled_token_is_expired() -> None:
    prompts = DuplicatePrompts()
    unknown = prompts.resolve("nope", DuplicateChoice.rename, user_id=123, is_whitelisted=True)
    token, _ = prompts.create(initiator_id=123, initiator_is_bot=False)
    prompts.resolve(token, DuplicateChoice.rename, user_id=123, is_whitelisted=True)
    settled = prompts.resolve(token, DuplicateChoice.cancel, user_id=123, is_whitelisted=True)

    assert unknown is ResolveOutcome.expired
    assert settled is ResolveOutcome.expired


class _FakeStatus:
    def __init__(self) -> None:
        self.edits: list[tuple[str, InlineKeyboardMarkup | None]] = []

    async def edit_text(
        self, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> object:
        self.edits.append((text, reply_markup))
        return None

    @property
    def texts(self) -> list[str]:
        return [text for text, _ in self.edits]


class _FakeDestination:
    def __init__(self, *, existing: bool) -> None:
        self.existing = existing
        self.uploads: list[tuple[str, bool]] = []

    async def file_exists(self, filename: str) -> bool:
        return self.existing

    async def upload(
        self,
        local: Path,
        filename: str,
        progress: Any = None,
        *,
        overwrite: bool = False,
    ) -> str:
        self.uploads.append((filename, overwrite))
        if self.existing and not overwrite:
            return "song (1).mp3"
        return filename

    async def scan(self) -> ScanResult:
        return ScanResult(ok=True, new_tracks=1)


def _context(settings: Settings, destination: _FakeDestination) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.bot_data = {
        "settings": settings,
        "destination": destination,
        "whitelist": Whitelist(settings),
        "duplicate_prompts": DuplicatePrompts(),
    }
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


def _patch_media_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(handlers, "resolve_filename", lambda message, settings: "song.mp3")

    async def _download(message: Message, settings: Settings, filename: str) -> Path:
        workdir = tmp_path / uuid.uuid4().hex
        workdir.mkdir(parents=True)
        local = workdir / filename
        local.write_bytes(b"x")
        return local

    monkeypatch.setattr(handlers, "download_media", _download)


def _start_pipeline(
    context: ContextTypes.DEFAULT_TYPE,
    status: _FakeStatus | None,
    initiator: User | None,
) -> asyncio.Task[str]:
    return asyncio.create_task(
        handlers._run_pipeline(
            cast(Message, MagicMock()),
            context,
            status=cast(Message, status),
            log_label="test",
            initiator=initiator,
        )
    )


async def _shown_keyboard(status: _FakeStatus) -> InlineKeyboardMarkup:
    for _ in range(200):
        for _, markup in status.edits:
            if markup is not None:
                return markup
        await asyncio.sleep(0.005)
    raise AssertionError("duplicate prompt was never shown")


def _button_data(markup: InlineKeyboardMarkup, label: str) -> str:
    for row in markup.inline_keyboard:
        for button in row:
            if button.text == label:
                return cast(str, button.callback_data)
    raise AssertionError(f"no button labelled {label!r}")


def _press(data: str, user_id: int) -> tuple[Update, AsyncMock]:
    query = MagicMock()
    query.data = data
    query.from_user = User(id=user_id, first_name="U", is_bot=False)
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return cast(Update, update), query.answer


async def test_pipeline_without_duplicate_skips_prompt(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_media_io(monkeypatch, tmp_path)
    destination = _FakeDestination(existing=False)
    status = _FakeStatus()
    context = _context(make_settings(run_scan=False), destination)

    reply = await _start_pipeline(context, status, User(id=123, first_name="U", is_bot=False))

    assert reply == "✅ Added song.mp3"
    assert destination.uploads == [("song.mp3", False)]
    assert all(markup is None for _, markup in status.edits)


async def test_rename_choice_uploads_with_suffix(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_media_io(monkeypatch, tmp_path)
    destination = _FakeDestination(existing=True)
    status = _FakeStatus()
    context = _context(make_settings(run_scan=False), destination)
    task = _start_pipeline(context, status, User(id=123, first_name="U", is_bot=False))

    keyboard = await _shown_keyboard(status)
    update, answer = _press(_button_data(keyboard, "Upload anyway (rename)"), 123)
    await handlers.on_duplicate_choice(update, context)

    assert await task == "✅ Added song (1).mp3"
    assert destination.uploads == [("song.mp3", False)]
    answer.assert_awaited_once_with()


async def test_overwrite_choice_replaces_file(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_media_io(monkeypatch, tmp_path)
    destination = _FakeDestination(existing=True)
    status = _FakeStatus()
    context = _context(make_settings(run_scan=False), destination)
    task = _start_pipeline(context, status, User(id=123, first_name="U", is_bot=False))

    keyboard = await _shown_keyboard(status)
    update, _ = _press(_button_data(keyboard, "Overwrite"), 123)
    await handlers.on_duplicate_choice(update, context)

    assert await task == "✅ Added song.mp3"
    assert destination.uploads == [("song.mp3", True)]


async def test_cancel_choice_uploads_nothing(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_media_io(monkeypatch, tmp_path)
    destination = _FakeDestination(existing=True)
    status = _FakeStatus()
    context = _context(make_settings(run_scan=False), destination)
    task = _start_pipeline(context, status, User(id=123, first_name="U", is_bot=False))

    keyboard = await _shown_keyboard(status)
    update, _ = _press(_button_data(keyboard, "Cancel"), 123)
    await handlers.on_duplicate_choice(update, context)

    assert await task == "🚫 Cancelled."
    assert destination.uploads == []


async def test_prompt_timeout_cancels_upload(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_media_io(monkeypatch, tmp_path)
    destination = _FakeDestination(existing=True)
    status = _FakeStatus()
    settings = make_settings(run_scan=False, duplicate_check_timeout_secs=0.05)
    context = _context(settings, destination)

    reply = await _start_pipeline(context, status, User(id=123, first_name="U", is_bot=False))

    assert reply == "🚫 song.mp3 already exists — not added (no answer in time)."
    assert destination.uploads == []


async def test_duplicate_without_status_channel_is_not_added(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_media_io(monkeypatch, tmp_path)
    destination = _FakeDestination(existing=True)
    context = _context(make_settings(run_scan=False), destination)

    reply = await _start_pipeline(context, None, User(id=123, first_name="U", is_bot=False))

    assert reply == "🚫 song.mp3 already exists — not added."
    assert destination.uploads == []


async def test_foreign_press_is_rejected_then_initiator_decides(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_media_io(monkeypatch, tmp_path)
    destination = _FakeDestination(existing=True)
    status = _FakeStatus()
    context = _context(make_settings(run_scan=False), destination)
    task = _start_pipeline(context, status, User(id=123, first_name="U", is_bot=False))

    keyboard = await _shown_keyboard(status)
    foreign, foreign_answer = _press(_button_data(keyboard, "Cancel"), 456)
    await handlers.on_duplicate_choice(foreign, context)
    own, _ = _press(_button_data(keyboard, "Overwrite"), 123)
    await handlers.on_duplicate_choice(own, context)

    assert await task == "✅ Added song.mp3"
    assert destination.uploads == [("song.mp3", True)]
    foreign_answer.assert_awaited_once_with("Only the requester can decide this upload.")


async def test_stale_press_answers_expired(make_settings: MakeSettings) -> None:
    context = _context(make_settings(run_scan=False), _FakeDestination(existing=False))
    update, answer = _press("dup:deadbeef:cancel", 123)

    await handlers.on_duplicate_choice(update, context)

    answer.assert_awaited_once_with("This prompt has expired.")
