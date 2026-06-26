import datetime as dt

from telegram import Audio, Chat, Document, Message, User

from nc_music_bot.handlers import describe_message


def _message(**kwargs: object) -> Message:
    return Message(
        message_id=1,
        date=dt.datetime.now(tz=dt.UTC),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, first_name="Tester", is_bot=False),
        **kwargs,  # type: ignore[arg-type]
    )


def test_describe_document() -> None:
    doc = Document(
        file_id="f", file_unique_id="u", file_name="Song.mp3", mime_type="application/octet-stream"
    )
    desc = describe_message(_message(document=doc))
    assert "document" in desc
    assert "Song.mp3" in desc
    assert "application/octet-stream" in desc


def test_describe_audio() -> None:
    audio = Audio(file_id="f", file_unique_id="u", duration=10, mime_type="audio/mpeg")
    assert describe_message(_message(audio=audio)).startswith("audio")


def test_describe_text_is_truncated() -> None:
    desc = describe_message(_message(text="x" * 200))
    assert desc.startswith("text")
    assert "…" in desc


def test_describe_short_text_is_verbatim() -> None:
    assert describe_message(_message(text="https://example.com")) == "text 'https://example.com'"


def test_describe_unknown_is_other() -> None:
    assert describe_message(_message()) == "other"
