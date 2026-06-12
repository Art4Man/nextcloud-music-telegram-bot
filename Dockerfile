FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Non-root; a real HOME is needed for the pinned known_hosts (~/.config/nc-music-bot).
RUN useradd --uid 1000 --create-home bot \
    && mkdir -p /home/bot/.config/nc-music-bot \
    && chown -R bot:bot /home/bot \
    && chown -R bot /app
USER bot

ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "nc_music_bot"]
