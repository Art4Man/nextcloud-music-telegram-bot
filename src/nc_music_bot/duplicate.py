import asyncio
import uuid
from dataclasses import dataclass
from enum import StrEnum

CALLBACK_PREFIX = "dup"


class DuplicateChoice(StrEnum):
    """What to do about a file that already exists on the destination."""

    rename = "rename"
    overwrite = "overwrite"
    cancel = "cancel"


class ResolveOutcome(StrEnum):
    """Result of a button press against a pending duplicate prompt."""

    resolved = "resolved"
    not_allowed = "not_allowed"
    expired = "expired"


def build_callback_data(token: str, choice: DuplicateChoice) -> str:
    return f"{CALLBACK_PREFIX}:{token}:{choice}"


def parse_callback_data(data: str) -> tuple[str, DuplicateChoice] | None:
    """Token and choice from a `dup:<token>:<choice>` payload; None if malformed."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    try:
        return parts[1], DuplicateChoice(parts[2])
    except ValueError:
        return None


@dataclass
class _Prompt:
    future: asyncio.Future[DuplicateChoice]
    initiator_id: int | None
    initiator_is_bot: bool


class DuplicatePrompts:
    """Pending duplicate decisions, keyed by a per-upload token.

    Lives in `bot_data`; every access happens on the single PTB event loop,
    so no locking is needed.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _Prompt] = {}

    def create(
        self, *, initiator_id: int | None, initiator_is_bot: bool
    ) -> tuple[str, asyncio.Future[DuplicateChoice]]:
        """Register a new prompt; the caller awaits the returned future."""
        token = uuid.uuid4().hex
        future: asyncio.Future[DuplicateChoice] = asyncio.get_running_loop().create_future()
        self._pending[token] = _Prompt(future, initiator_id, initiator_is_bot)
        return token, future

    def resolve(
        self, token: str, choice: DuplicateChoice, *, user_id: int, is_whitelisted: bool
    ) -> ResolveOutcome:
        """Settle the prompt's future with `choice` if `user_id` may decide it."""
        prompt = self._pending.get(token)
        if prompt is None or prompt.future.done():
            return ResolveOutcome.expired
        if not _may_decide(prompt, user_id, is_whitelisted):
            return ResolveOutcome.not_allowed
        del self._pending[token]
        prompt.future.set_result(choice)
        return ResolveOutcome.resolved

    def discard(self, token: str) -> None:
        """Drop a prompt that timed out or finished; safe to call twice."""
        self._pending.pop(token, None)


def _may_decide(prompt: _Prompt, user_id: int, is_whitelisted: bool) -> bool:
    """Human initiators decide their own uploads; bot-relayed ones fall to any whitelisted user."""
    if prompt.initiator_id is not None and not prompt.initiator_is_bot:
        return user_id == prompt.initiator_id
    return is_whitelisted
