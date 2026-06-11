from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nc_music_bot.config import Settings

MakeSettings = Callable[..., Settings]


@pytest.fixture
def make_settings(tmp_path: Path) -> MakeSettings:
    # A pre-existing known_hosts file keeps tests away from first-connect key pinning.
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("example-host ssh-ed25519 AAAA-test\n")

    def _make(**overrides: Any) -> Settings:
        base: dict[str, Any] = {
            "allowed_user_ids": "123,456",
            "dest_host": "example-host",
            "dest_ssh_password": "pw",
            "dest_path": "/srv/music",
            "nextcloud_scan_path": "admin/files/Music",
            "dest_known_hosts": known_hosts,
            "temp_dir": tmp_path / "tmp",
        }
        base.update(overrides)
        # _env_file=None keeps a developer's real .env out of the tests.
        return Settings(_env_file=None, **base)  # type: ignore[call-arg]

    return _make
