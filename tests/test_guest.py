import datetime as dt
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Audio, Chat, Document, InlineQueryResultArticle, Message, Update, User
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nc_music_bot import handlers
from nc_music_bot.download import is_supported_audio
from nc_music_bot.errors import UserFacingError
from nc_music_bot.whitelist import Whitelist

from .conftest import MakeSettings


def _message(**kwargs: Any) -> Message:
    return Message(
        message_id=1,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=1, type="private"),
        **kwargs,
    )


def _audio() -> Audio:
    return Audio(file_id="f", file_unique_id="u", duration=10, mime_type="audio/mpeg")


def _doc(name: str | None, mime: str | None) -> Document:
    return Document(file_id="f", file_unique_id="u", file_name=name, mime_type=mime)


def test_is_supported_audio_accepts_music() -> None:
    assert is_supported_audio(_message(audio=_audio()))


def test_is_supported_audio_accepts_audio_mime_document() -> None:
    assert is_supported_audio(_message(document=_doc("Song", "audio/flac")))


def test_is_supported_audio_accepts_known_extension_without_mime() -> None:
    assert is_supported_audio(_message(document=_doc("Track.flac", "application/octet-stream")))


def test_is_supported_audio_rejects_other_documents() -> None:
    assert not is_supported_audio(_message(document=_doc("report.pdf", "application/pdf")))


def test_is_supported_audio_rejects_plain_text() -> None:
    assert not is_supported_audio(_message(text="https://example.com/song"))


class _FakeStatus:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text: str) -> object:
        self.edits.append(text)
        return None


def _make_bot(*, dm: object | Exception) -> MagicMock:
    bot = MagicMock()
    bot.answer_guest_query = AsyncMock()
    if isinstance(dm, Exception):
        bot.send_message = AsyncMock(side_effect=dm)
    else:
        bot.send_message = AsyncMock(return_value=dm)
    return bot


def _guest_update(*, caller_id: int, reply: Message | None, bot: MagicMock) -> Update:
    gm = Message(
        message_id=2,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=caller_id, type="private"),
        guest_query_id="q1",
        guest_bot_caller_user=User(id=caller_id, first_name="Caller", is_bot=False),
        guest_bot_caller_chat=Chat(id=-100, type="group"),
        reply_to_message=reply,
    )
    gm.set_bot(cast(Any, bot))
    return Update(update_id=1, guest_message=gm)


def _context(whitelist: Whitelist, bot: MagicMock) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.bot_data = {"whitelist": whitelist}
    ctx.bot = bot
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


def _answered_text(bot: MagicMock) -> str:
    call = bot.answer_guest_query.await_args
    for value in list(call.args) + list(call.kwargs.values()):
        if isinstance(value, InlineQueryResultArticle):
            content = cast(Any, value.input_message_content)
            return cast(str, content.message_text)
    raise AssertionError("answer_guest_query was not called with an article result")


async def test_non_whitelisted_caller_is_rejected(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = AsyncMock()
    monkeypatch.setattr(handlers, "_run_pipeline", ran)
    bot = _make_bot(dm=_FakeStatus())
    wl = Whitelist(make_settings())
    update = _guest_update(caller_id=999, reply=_message(audio=_audio()), bot=bot)

    await handlers.handle_guest(update, _context(wl, bot))

    ran.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    assert bot.answer_guest_query.await_count == 1
    assert "Not authorized" in _answered_text(bot)
    assert "999" in _answered_text(bot)


async def test_reply_without_audio_gets_the_hint(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = AsyncMock()
    monkeypatch.setattr(handlers, "_run_pipeline", ran)
    bot = _make_bot(dm=_FakeStatus())
    wl = Whitelist(make_settings())
    update = _guest_update(caller_id=123, reply=_message(text="not a song"), bot=bot)

    await handlers.handle_guest(update, _context(wl, bot))

    ran.assert_not_awaited()
    assert bot.answer_guest_query.await_count == 1
    assert _answered_text(bot) == handlers.GUEST_HINT_TEXT


async def test_missing_reply_gets_the_hint(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_run_pipeline", AsyncMock())
    bot = _make_bot(dm=_FakeStatus())
    wl = Whitelist(make_settings())
    update = _guest_update(caller_id=123, reply=None, bot=bot)

    await handlers.handle_guest(update, _context(wl, bot))

    assert _answered_text(bot) == handlers.GUEST_HINT_TEXT


async def test_supported_audio_runs_pipeline_and_answers_once(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = "✅ Added Song.mp3 — 1 track(s) indexed"

    async def _pipeline(
        media_message: Message, context: object, *, status: Any, log_label: str, initiator: Any
    ) -> str:
        assert media_message.audio is not None
        if status is not None:
            await status.edit_text("📤 Uploading …")
        return final

    monkeypatch.setattr(handlers, "_run_pipeline", _pipeline)
    dm = _FakeStatus()
    bot = _make_bot(dm=dm)
    wl = Whitelist(make_settings())
    update = _guest_update(caller_id=123, reply=_message(audio=_audio()), bot=bot)

    await handlers.handle_guest(update, _context(wl, bot))

    bot.send_message.assert_awaited_once()
    assert bot.answer_guest_query.await_count == 1
    assert _answered_text(bot) == final
    assert dm.edits[-1] == final


async def test_pipeline_user_facing_error_is_relayed(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _pipeline(
        media_message: Message, context: object, *, status: Any, log_label: str, initiator: Any
    ) -> str:
        raise UserFacingError("File is 999 MB — over the limit.")

    monkeypatch.setattr(handlers, "_run_pipeline", _pipeline)
    bot = _make_bot(dm=_FakeStatus())
    wl = Whitelist(make_settings())
    update = _guest_update(caller_id=123, reply=_message(audio=_audio()), bot=bot)

    await handlers.handle_guest(update, _context(wl, bot))

    assert bot.answer_guest_query.await_count == 1
    assert _answered_text(bot) == "❌ File is 999 MB — over the limit."


async def test_unreachable_dm_is_swallowed_and_reply_still_sent(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = "✅ Added Song.mp3"

    async def _pipeline(
        media_message: Message, context: object, *, status: Any, log_label: str, initiator: Any
    ) -> str:
        assert status is None
        return final

    monkeypatch.setattr(handlers, "_run_pipeline", _pipeline)
    bot = _make_bot(dm=Forbidden("bot can't initiate conversation with a user"))
    wl = Whitelist(make_settings())
    update = _guest_update(caller_id=123, reply=_message(audio=_audio()), bot=bot)

    await handlers.handle_guest(update, _context(wl, bot))

    assert bot.answer_guest_query.await_count == 1
    assert _answered_text(bot) == final
