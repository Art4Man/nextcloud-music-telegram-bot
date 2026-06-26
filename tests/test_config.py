import pytest
from pydantic import ValidationError

from nc_music_bot.config import CLOUD_API_MAX_MB, MB, AppMode

from .conftest import MakeSettings


def test_user_ids_parsed_from_csv(make_settings: MakeSettings) -> None:
    assert make_settings().allowed_user_ids == frozenset({123, 456})


def test_user_ids_tolerate_spaces(make_settings: MakeSettings) -> None:
    settings = make_settings(allowed_user_ids="1, 2  ,3")
    assert settings.allowed_user_ids == frozenset({1, 2, 3})


def test_empty_user_ids_rejected(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(allowed_user_ids="   ")


def test_dest_path_trailing_slash_stripped(make_settings: MakeSettings) -> None:
    assert make_settings(dest_path="/srv/music/").dest_path == "/srv/music"


def test_relative_dest_path_rejected(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(dest_path="srv/music")


def test_key_or_password_required(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(dest_ssh_password=None)


def test_key_path_alone_suffices_and_expands_user(make_settings: MakeSettings) -> None:
    settings = make_settings(dest_ssh_password=None, dest_ssh_key_path="~/keys/bot_id")
    assert settings.dest_ssh_key_path is not None
    assert not str(settings.dest_ssh_key_path).startswith("~")


def test_scan_path_required_while_scanning(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(nextcloud_scan_path=None)


def test_scan_path_optional_when_scan_disabled(make_settings: MakeSettings) -> None:
    settings = make_settings(nextcloud_scan_path=None, run_scan=False)
    assert settings.nextcloud_scan_path is None


def test_production_is_the_default_mode(make_settings: MakeSettings) -> None:
    assert make_settings().app_mode is AppMode.production


def test_production_requires_dest_host(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(dest_host="")


def test_production_requires_dest_path(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(dest_path="")


def test_stage_mode_needs_no_destination_config(make_settings: MakeSettings) -> None:
    settings = make_settings(
        app_mode="stage",
        dest_host="",
        dest_path="",
        dest_ssh_password=None,
        nextcloud_scan_path=None,
    )
    assert settings.app_mode is AppMode.stage
    assert settings.dest_host == ""


def test_stage_dir_defaults_under_temp_dir(make_settings: MakeSettings) -> None:
    settings = make_settings(app_mode="stage")
    assert settings.effective_stage_dir == settings.temp_dir / "stage"


def test_stage_dir_override_and_expands_user(make_settings: MakeSettings) -> None:
    settings = make_settings(app_mode="stage", stage_dir="~/music-stage")
    assert settings.stage_dir is not None
    assert not str(settings.stage_dir).startswith("~")
    assert settings.effective_stage_dir == settings.stage_dir


def test_cloud_api_caps_downloads_at_20mb(make_settings: MakeSettings) -> None:
    assert make_settings().effective_max_bytes == CLOUD_API_MAX_MB * MB


def test_local_bot_api_lifts_the_cap(make_settings: MakeSettings) -> None:
    settings = make_settings(telegram_api_base_url="http://bot-api:8081/")
    assert settings.telegram_api_base_url == "http://bot-api:8081"
    assert settings.effective_max_bytes == settings.max_file_bytes
