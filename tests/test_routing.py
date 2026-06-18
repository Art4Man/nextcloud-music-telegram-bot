import datetime as dt

from telegram import Audio, Chat, Document, Message, Update, User

from nc_music_bot.bot import AUDIO_MESSAGE
from nc_music_bot.whitelist import Whitelist

from .conftest import MakeSettings


def _update(
    *,
    document: Document | None = None,
    audio: Audio | None = None,
    user_id: int = 123,
) -> Update:
    message = Message(
        message_id=1,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, first_name="Tester", is_bot=False),
        document=document,
        audio=audio,
    )
    return Update(update_id=1, message=message)


def _audio_document(name: str, mime: str | None, user_id: int = 123) -> Update:
    doc = Document(file_id="f", file_unique_id="u", file_name=name, mime_type=mime)
    return _update(document=doc, user_id=user_id)


def test_audio_document_with_nonaudio_mime_routes_to_handle_audio(
    make_settings: MakeSettings,
) -> None:
    # Music forwarded from a downloader bot arrives as a document with a bogus
    # mime type but an audio extension — filters.Document.AUDIO misses it.
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
