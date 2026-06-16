"""Typed configuration loaded from environment variables / `.env`.

Every knob is documented in `.env.example`; cross-field rules live in the
validators below so a misconfigured deployment fails at startup, not mid-upload.
"""

import tempfile
from pathlib import Path
from typing import Annotated, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MB = 1024 * 1024

# The standard (cloud) Bot API refuses `getFile` for anything larger than this.
# Only a self-hosted telegram-bot-api lifts the cap (to 2 GB).
CLOUD_API_MAX_MB = 20


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────────────────
    # Empty is tolerated so `--check` can run without a token; `run` enforces it.
    telegram_bot_token: str = ""
    allowed_user_ids: Annotated[frozenset[int], NoDecode]
    source_bot_usernames: Annotated[frozenset[str], NoDecode] = frozenset()
    whitelist_store_path: Path | None = None
    max_file_mb: int = 2000
    telegram_api_base_url: str | None = None

    # ── Destination, reached over Tailscale ───────────────────────────────
    dest_host: str
    dest_ssh_port: int = 22
    dest_ssh_user: str = "root"
    dest_ssh_key_path: Path | None = None
    dest_ssh_password: str | None = None
    dest_known_hosts: Path | None = None
    dest_path: str

    # ── Nextcloud scan after each upload ──────────────────────────────────
    run_scan: bool = True
    nextcloud_occ: str = "nextcloud.occ"
    nextcloud_user: str = "admin"
    nextcloud_scan_path: str | None = None

    # ── Misc ──────────────────────────────────────────────────────────────
    temp_dir: Path = Path(tempfile.gettempdir()) / "nc-music-bot"
    log_level: str = "INFO"

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _parse_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            parts = value.replace(",", " ").split()
            if not parts:
                raise ValueError("ALLOWED_USER_IDS must contain at least one Telegram user ID")
            return frozenset(int(part) for part in parts)
        return value

    @field_validator("source_bot_usernames", mode="before")
    @classmethod
    def _parse_source_bots(cls, value: object) -> object:
        if isinstance(value, str):
            parts = value.replace(",", " ").split()
            return frozenset(part.lstrip("@").lower() for part in parts)
        return value

    @field_validator("telegram_api_base_url")
    @classmethod
    def _normalize_base_url(cls, value: str | None) -> str | None:
        return value.rstrip("/") or None if value else None

    @field_validator("dest_path")
    @classmethod
    def _require_absolute_dest(cls, value: str) -> str:
        value = value.rstrip("/") or "/"
        if not value.startswith("/"):
            raise ValueError("DEST_PATH must be an absolute path on the destination")
        return value

    @field_validator("dest_ssh_key_path", "dest_known_hosts", "temp_dir", "whitelist_store_path")
    @classmethod
    def _expand_user(cls, value: Path | None) -> Path | None:
        return value.expanduser() if value is not None else None

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Self:
        if not self.dest_ssh_key_path and not self.dest_ssh_password:
            raise ValueError(
                "set DEST_SSH_KEY_PATH (recommended) or DEST_SSH_PASSWORD to authenticate"
            )
        if self.run_scan and not self.nextcloud_scan_path:
            raise ValueError(
                "NEXTCLOUD_SCAN_PATH is required while RUN_SCAN=true "
                "(e.g. admin/files/Music); set RUN_SCAN=false for plain SFTP destinations"
            )
        return self

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * MB

    @property
    def effective_max_bytes(self) -> int:
        """The real download cap: cloud Bot API tops out at 20 MB without a local api."""
        if self.telegram_api_base_url:
            return self.max_file_bytes
        return min(self.max_file_bytes, CLOUD_API_MAX_MB * MB)
