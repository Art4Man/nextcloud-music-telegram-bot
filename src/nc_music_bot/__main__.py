"""Entrypoint: `python -m nc_music_bot [run|check]` (or `--check`)."""

import argparse
import asyncio
import sys

from pydantic import ValidationError

from .config import AppMode, Settings
from .logging_conf import configure_logging


def _load_settings() -> Settings:
    try:
        # Required fields arrive from the environment / .env at runtime.
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        print("Configuration error:", file=sys.stderr)
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"]) or "settings"
            print(f"  {loc.upper()}: {err['msg']}", file=sys.stderr)
        print("\nSee .env.example for the full reference.", file=sys.stderr)
        raise SystemExit(2) from exc


async def _run_check(settings: Settings) -> int:
    from .destination import choose_destination

    if settings.app_mode is AppMode.stage:
        print(f"Destination: stage dir {settings.effective_stage_dir}")
    else:
        print(
            f"Destination: {settings.dest_ssh_user}@{settings.dest_host}:"
            f"{settings.dest_ssh_port}{settings.dest_path}"
        )
    if not settings.telegram_bot_token:
        print("note: TELEGRAM_BOT_TOKEN is empty — fine for --check, required to run.")
    items = await choose_destination(settings).check()
    for item in items:
        mark = "ok " if item.ok else "FAIL"
        print(f"[{mark}] {item.label}" + (f" — {item.detail}" if item.detail else ""))
    return 0 if all(item.ok for item in items) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nc-music-bot",
        description="Telegram → Nextcloud music relay over Tailscale.",
    )
    parser.add_argument("command", nargs="?", choices=["run", "check"], default="run")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate config and test the destination connection, then exit",
    )
    args = parser.parse_args(argv)

    settings = _load_settings()
    configure_logging(settings.log_level)

    if args.check or args.command == "check":
        return asyncio.run(_run_check(settings))

    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is required to run the bot.", file=sys.stderr)
        return 2

    from .bot import run_bot

    run_bot(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
