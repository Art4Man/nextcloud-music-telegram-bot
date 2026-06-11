import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    # These drown out the useful lines at INFO.
    for name in ("httpx", "asyncssh", "telegram", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
