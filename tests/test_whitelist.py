import json
from pathlib import Path

import pytest

from nc_music_bot.whitelist import Whitelist

from .conftest import MakeSettings


def test_seeds_from_settings(make_settings: MakeSettings) -> None:
    wl = Whitelist(make_settings(source_bot_usernames="@Spotify_DL_bot"))
    assert wl.list_users() == [123, 456]
    assert wl.list_bots() == ["spotify_dl_bot"]


def test_admins_are_env_user_ids(make_settings: MakeSettings) -> None:
    wl = Whitelist(make_settings())
    assert wl.is_admin(123)
    assert not wl.is_admin(789)
    assert not wl.is_admin(None)


def test_allow_and_deny_user(make_settings: MakeSettings) -> None:
    wl = Whitelist(make_settings())
    assert wl.allow_user(789)
    assert 789 in wl.users.user_ids
    assert not wl.allow_user(789)
    assert wl.deny_user(789)
    assert 789 not in wl.users.user_ids
    assert not wl.deny_user(789)


def test_cannot_deny_bootstrap_admin(make_settings: MakeSettings) -> None:
    wl = Whitelist(make_settings())
    with pytest.raises(ValueError):
        wl.deny_user(123)
    assert 123 in wl.users.user_ids


def test_bot_matching_is_case_insensitive(make_settings: MakeSettings) -> None:
    wl = Whitelist(make_settings())
    assert wl.add_bot("@Spotify_DL_bot") == "spotify_dl_bot"
    assert wl.add_bot("spotify_dl_bot") is None

    class _User:
        username = "Spotify_DL_bot"

    class _Message:
        from_user = _User()

    assert wl.bots.filter(_Message())  # type: ignore[arg-type]
    assert wl.remove_bot("@SPOTIFY_DL_BOT") == "spotify_dl_bot"
    assert not wl.bots.filter(_Message())  # type: ignore[arg-type]


def test_persistence_round_trip(make_settings: MakeSettings, tmp_path: Path) -> None:
    store = tmp_path / "wl.json"
    wl = Whitelist(make_settings(whitelist_store_path=store))
    wl.allow_user(789)
    wl.add_bot("@NewBot")

    saved = json.loads(store.read_text("utf-8"))
    assert 789 in saved["users"]
    assert "newbot" in saved["bots"]

    reloaded = Whitelist(make_settings(whitelist_store_path=store))
    assert 789 in reloaded.users.user_ids
    assert "newbot" in reloaded.list_bots()
