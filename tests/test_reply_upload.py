import datetime as dt
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Audio, Chat, Message, MessageEntity, Update, User
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nc_music_bot import handlers
from nc_music_bot.whitelist import Whitelist

from .conftest import MakeSettings

BOT_USERNAME = "tg_to_nc_bot"


class _FakeStatus:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text: str) -> object:
        self.edits.append(text)
        return None


def _audio_message() -> Message:
    return Message(
        message_id=10,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=555, first_name="Friend", is_bot=False),
        audio=Audio(file_id="f", file_unique_id="u", duration=10, mime_type="audio/mpeg"),
    )


def _make_bot(*, status: object | Exception) -> MagicMock:
    bot = MagicMock()
    bot.username = BOT_USERNAME
    if isinstance(status, Exception):
        bot.send_message = AsyncMock(side_effect=status)
    else:
        bot.send_message = AsyncMock(return_value=status)
    return bot


def _sent_text(bot: MagicMock) -> str:
    call = bot.send_message.await_args
    if "text" in call.kwargs:
        return cast(str, call.kwargs["text"])
    return cast(str, call.args[1] if len(call.args) > 1 else call.args[0])


def _trigger_update(*, text: str, mention: bool, reply: Message | None, bot: MagicMock) -> Update:
    entities: tuple[MessageEntity, ...] = ()
    if mention:
        handle = f"@{BOT_USERNAME}"
        offset = text.index(handle)
        entities = (MessageEntity(type=MessageEntity.MENTION, offset=offset, length=len(handle)),)
    msg = Message(
        message_id=2,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=123, first_name="Owner", is_bot=False),
        text=text,
        entities=entities,
        reply_to_message=reply,
    )
    msg.set_bot(cast(Any, bot))
    return Update(update_id=1, message=msg)


def _context(whitelist: Whitelist, bot: MagicMock) -> ContextTypes.DEFAULT_TYPE:
    ctx = MagicMock()
    ctx.bot_data = {"whitelist": whitelist}
    ctx.bot = bot
    return cast(ContextTypes.DEFAULT_TYPE, ctx)


async def test_reply_to_song_with_mention_uploads(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = "✅ Added Song.mp3 — 1 track(s) indexed"
    seen: dict[str, Message] = {}

    async def _pipeline(target: Message, context: object, *, status: Any, log_label: str) -> str:
        seen["target"] = target
        return final

    monkeypatch.setattr(handlers, "_run_pipeline", _pipeline)
    status = _FakeStatus()
    bot = _make_bot(status=status)
    audio = _audio_message()
    update = _trigger_update(text=f"@{BOT_USERNAME}", mention=True, reply=audio, bot=bot)

    await handlers.handle_unsupported(update, _context(Whitelist(make_settings()), bot))

    assert seen["target"] is audio
    assert status.edits[-1] == final
    sent = [c.args[1] for c in bot.send_message.await_args_list if len(c.args) > 1]
    assert any("mentioned me on a song" in text for text in sent)


async def test_reply_to_song_without_mention_gets_hint(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = AsyncMock()
    monkeypatch.setattr(handlers, "_run_pipeline", ran)
    bot = _make_bot(status=_FakeStatus())
    update = _trigger_update(text="nice track", mention=False, reply=_audio_message(), bot=bot)

    await handlers.handle_unsupported(update, _context(Whitelist(make_settings()), bot))

    ran.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    assert "doesn't look like an audio file" in _sent_text(bot)


async def test_mention_replying_to_non_audio_gets_hint(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = AsyncMock()
    monkeypatch.setattr(handlers, "_run_pipeline", ran)
    bot = _make_bot(status=_FakeStatus())
    not_audio = Message(
        message_id=11,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=-100, type="supergroup"),
        text="just chatting",
    )
    update = _trigger_update(text=f"@{BOT_USERNAME}", mention=True, reply=not_audio, bot=bot)

    await handlers.handle_unsupported(update, _context(Whitelist(make_settings()), bot))

    ran.assert_not_awaited()
    assert "doesn't look like an audio file" in _sent_text(bot)


async def test_kicked_bot_does_not_raise(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = AsyncMock()
    monkeypatch.setattr(handlers, "_run_pipeline", ran)
    bot = _make_bot(status=Forbidden("bot was kicked from the group chat"))
    update = _trigger_update(text=f"@{BOT_USERNAME}", mention=True, reply=_audio_message(), bot=bot)

    await handlers.handle_unsupported(update, _context(Whitelist(make_settings()), bot))

    ran.assert_not_awaited()
