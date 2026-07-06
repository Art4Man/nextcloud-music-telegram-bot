from nc_music_bot.bot import build_application

from .conftest import MakeSettings


def test_application_processes_updates_concurrently(make_settings: MakeSettings) -> None:
    app = build_application(make_settings(telegram_bot_token="123:abc"))

    assert app.concurrent_updates > 1
