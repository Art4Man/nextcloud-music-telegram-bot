from nc_music_bot.auth import allowed_users_filter, is_allowed

from .conftest import MakeSettings


def test_whitelisted_user_allowed(make_settings: MakeSettings) -> None:
    assert is_allowed(123, make_settings())


def test_unknown_user_denied(make_settings: MakeSettings) -> None:
    assert not is_allowed(999, make_settings())


def test_missing_user_denied(make_settings: MakeSettings) -> None:
    assert not is_allowed(None, make_settings())


def test_filter_carries_whitelist(make_settings: MakeSettings) -> None:
    flt = allowed_users_filter(make_settings())
    assert flt.user_ids == frozenset({123, 456})
