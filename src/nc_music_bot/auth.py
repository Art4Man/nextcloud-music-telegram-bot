"""Telegram user-ID whitelist — checked via handler filters on every message."""

from telegram.ext import filters

from .config import Settings


def is_allowed(user_id: int | None, settings: Settings) -> bool:
    return user_id is not None and user_id in settings.allowed_user_ids


def allowed_users_filter(settings: Settings) -> filters.User:
    return filters.User(user_id=settings.allowed_user_ids)
