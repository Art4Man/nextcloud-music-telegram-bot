"""Runtime-mutable whitelists for allowed users and trusted source bots.

The PTB filter objects held here are the same instances wired into the audio
handlers, so mutating them takes effect immediately. Changes are also persisted
to a JSON store (when `WHITELIST_STORE_PATH` is set) so they survive restarts;
the env-provided values always seed the whitelist on startup.
"""

import json
import logging
from pathlib import Path

from telegram import Message
from telegram.ext import filters

from .config import Settings

log = logging.getLogger(__name__)


class SourceBotFilter(filters.MessageFilter):
    """Case-insensitive match on the sender's bot username."""

    def __init__(self, usernames: set[str]) -> None:
        super().__init__(name="SourceBotFilter", data_filter=False)
        self.usernames = usernames

    def filter(self, message: Message) -> bool:
        user = message.from_user
        return bool(user and user.username and user.username.lower() in self.usernames)


def _normalize_username(username: str) -> str:
    return username.lstrip("@").lower()


class Whitelist:
    def __init__(self, settings: Settings) -> None:
        self.admin_ids: frozenset[int] = settings.allowed_user_ids
        self.store_path: Path | None = settings.whitelist_store_path
        self.users = filters.User(user_id=set(settings.allowed_user_ids))
        self._bot_usernames: set[str] = set(settings.source_bot_usernames)
        self.bots = SourceBotFilter(self._bot_usernames)
        self._load()

    @property
    def audio_filter(self) -> filters.BaseFilter:
        return self.users | self.bots

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids

    def is_allowed_user(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.users.user_ids

    def list_users(self) -> list[int]:
        return sorted(self.users.user_ids)

    def list_bots(self) -> list[str]:
        return sorted(self._bot_usernames)

    def allow_user(self, user_id: int) -> bool:
        if user_id in self.users.user_ids:
            return False
        self.users.add_user_ids(user_id)
        self._save()
        return True

    def deny_user(self, user_id: int) -> bool:
        if user_id in self.admin_ids:
            raise ValueError("that ID is a bootstrap admin (ALLOWED_USER_IDS) and can't be removed")
        if user_id not in self.users.user_ids:
            return False
        self.users.remove_user_ids(user_id)
        self._save()
        return True

    def add_bot(self, username: str) -> str | None:
        name = _normalize_username(username)
        if not name or name in self._bot_usernames:
            return None
        self._bot_usernames.add(name)
        self._save()
        return name

    def remove_bot(self, username: str) -> str | None:
        name = _normalize_username(username)
        if name not in self._bot_usernames:
            return None
        self._bot_usernames.discard(name)
        self._save()
        return name

    def _load(self) -> None:
        if not self.store_path or not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text("utf-8"))
        except (OSError, ValueError):
            log.exception("Failed to read whitelist store %s", self.store_path)
            return
        for uid in data.get("users", []):
            self.users.add_user_ids(int(uid))
        for name in data.get("bots", []):
            self._bot_usernames.add(_normalize_username(str(name)))

    def _save(self) -> None:
        if not self.store_path:
            return
        payload = {"users": self.list_users(), "bots": self.list_bots()}
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self.store_path.write_text(json.dumps(payload, indent=2), "utf-8")
        except OSError:
            log.exception("Failed to write whitelist store %s", self.store_path)
