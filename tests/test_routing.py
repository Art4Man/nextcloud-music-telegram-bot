import datetime as dt

from telegram import Audio, Chat, Document, Message, MessageEntity, Update, User
from telegram.ext import filters

from nc_music_bot.bot import AUDIO_MESSAGE
from nc_music_bot.whitelist import Whitelist

from .conftest import MakeSettings


def _update(
    *,
    document: Document | None = None,
    audio: Audio | None = None,
    text: str | None = None,
    entities: tuple[MessageEntity, ...] = (),
    user_id: int = 123,
) -> Update:
    message = Message(
        message_id=1,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, first_name="Tester", is_bot=False),
        document=document,
        audio=audio,
        text=text,
        entities=entities,
    )
    return Update(update_id=1, message=message)


def _audio_document(name: str, mime: str | None, user_id: int = 123) -> Update:
    doc = Document(file_id="f", file_unique_id="u", file_name=name, mime_type=mime)
    return _update(document=doc, user_id=user_id)


def test_audio_document_with_nonaudio_mime_routes_to_handle_audio(
    make_settings: MakeSettings,
) -> None:
    update = _audio_document("Artist - Title.mp3", "application/octet-stream", user_id=123)
    trusted = Whitelist(make_settings()).audio_filter

    assert (AUDIO_MESSAGE & trusted).check_update(update)
    assert not (AUDIO_MESSAGE & ~trusted).check_update(update)


def test_audio_document_without_mime_still_matches() -> None:
    assert AUDIO_MESSAGE.check_update(_audio_document("Track.flac", None))


def test_non_audio_document_is_not_accepted() -> None:
    assert not AUDIO_MESSAGE.check_update(_audio_document("report.pdf", "application/pdf"))


def test_unauthorized_user_routes_to_handle_unauthorized(make_settings: MakeSettings) -> None:
    update = _audio_document("Song.mp3", "application/octet-stream", user_id=999)
    trusted = Whitelist(make_settings()).audio_filter

    assert not (AUDIO_MESSAGE & trusted).check_update(update)
    assert (AUDIO_MESSAGE & ~trusted).check_update(update)


def test_trusted_user_link_routes_to_handle_unsupported(make_settings: MakeSettings) -> None:
    update = _update(text="https://example.com/song", user_id=123)
    trusted = Whitelist(make_settings()).audio_filter
    fallback = trusted & ~AUDIO_MESSAGE & ~filters.COMMAND

    assert fallback.check_update(update)
    assert not (AUDIO_MESSAGE & trusted).check_update(update)


def test_trusted_user_command_is_not_caught_by_fallback(make_settings: MakeSettings) -> None:
    command = MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=5)
    update = _update(text="/help", entities=(command,), user_id=123)
    trusted = Whitelist(make_settings()).audio_filter
    fallback = trusted & ~AUDIO_MESSAGE & ~filters.COMMAND

    assert not fallback.check_update(update)


def test_untrusted_user_link_is_silent(make_settings: MakeSettings) -> None:
    update = _update(text="https://example.com/song", user_id=999)
    trusted = Whitelist(make_settings()).audio_filter
    fallback = trusted & ~AUDIO_MESSAGE & ~filters.COMMAND

    assert not fallback.check_update(update)
