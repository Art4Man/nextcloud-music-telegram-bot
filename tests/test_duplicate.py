from nc_music_bot.duplicate import (
    DuplicateChoice,
    DuplicatePrompts,
    ResolveOutcome,
    build_callback_data,
    parse_callback_data,
)


def test_callback_data_round_trip() -> None:
    data = build_callback_data("abc123", DuplicateChoice.overwrite)
    assert parse_callback_data(data) == ("abc123", DuplicateChoice.overwrite)


def test_parse_rejects_malformed_data() -> None:
    assert parse_callback_data("dup:abc") is None
    assert parse_callback_data("other:abc:rename") is None
    assert parse_callback_data("dup:abc:explode") is None
    assert parse_callback_data("") is None


async def test_initiator_resolves_own_prompt() -> None:
    prompts = DuplicatePrompts()
    token, future = prompts.create(initiator_id=123, initiator_is_bot=False)

    outcome = prompts.resolve(token, DuplicateChoice.rename, user_id=123, is_whitelisted=True)

    assert outcome is ResolveOutcome.resolved
    assert await future is DuplicateChoice.rename


async def test_other_users_cannot_decide_a_human_prompt() -> None:
    prompts = DuplicatePrompts()
    token, future = prompts.create(initiator_id=123, initiator_is_bot=False)

    outcome = prompts.resolve(token, DuplicateChoice.cancel, user_id=456, is_whitelisted=True)

    assert outcome is ResolveOutcome.not_allowed
    assert not future.done()


async def test_whitelisted_user_decides_bot_initiated_prompt() -> None:
    prompts = DuplicatePrompts()
    token, future = prompts.create(initiator_id=999, initiator_is_bot=True)

    stranger = prompts.resolve(token, DuplicateChoice.overwrite, user_id=1, is_whitelisted=False)
    member = prompts.resolve(token, DuplicateChoice.overwrite, user_id=123, is_whitelisted=True)

    assert stranger is ResolveOutcome.not_allowed
    assert member is ResolveOutcome.resolved
    assert await future is DuplicateChoice.overwrite


async def test_unknown_or_settled_token_is_expired() -> None:
    prompts = DuplicatePrompts()
    unknown = prompts.resolve("nope", DuplicateChoice.rename, user_id=123, is_whitelisted=True)
    token, _ = prompts.create(initiator_id=123, initiator_is_bot=False)
    prompts.resolve(token, DuplicateChoice.rename, user_id=123, is_whitelisted=True)
    settled = prompts.resolve(token, DuplicateChoice.cancel, user_id=123, is_whitelisted=True)

    assert unknown is ResolveOutcome.expired
    assert settled is ResolveOutcome.expired
