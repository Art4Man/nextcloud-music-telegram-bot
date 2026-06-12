import asyncio

from nc_music_bot.progress import UploadProgressReporter, format_upload_progress

MB = 1024 * 1024


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeStatus:
    """Records edits; an optional gate keeps an edit "in flight" until released."""

    def __init__(self) -> None:
        self.edits: list[str] = []
        self.gate: asyncio.Event | None = None

    async def edit_text(self, text: str) -> object:
        if self.gate is not None:
            await self.gate.wait()
        self.edits.append(text)
        return None


def test_format_renders_bar_percent_and_megabytes() -> None:
    assert format_upload_progress("song.mp3", 0, 20 * MB) == (
        "📤 Uploading song.mp3 …\n░░░░░░░░░░ 0% (0.0 / 20.0 MB)"
    )
    assert format_upload_progress("song.mp3", 13 * MB, 25 * MB) == (
        "📤 Uploading song.mp3 …\n▓▓▓▓▓░░░░░ 52% (13.0 / 25.0 MB)"
    )
    assert format_upload_progress("song.mp3", 20 * MB, 20 * MB) == (
        "📤 Uploading song.mp3 …\n▓▓▓▓▓▓▓▓▓▓ 100% (20.0 / 20.0 MB)"
    )


def test_format_without_a_total_falls_back_to_plain_line() -> None:
    assert format_upload_progress("song.mp3", 5 * MB, 0) == "📤 Uploading song.mp3 …"


async def test_rapid_callbacks_are_throttled_to_one_edit() -> None:
    clock = FakeClock()
    status = FakeStatus()
    reporter = UploadProgressReporter(status, "song.mp3", now=clock)

    for copied in range(0, 10 * MB, MB):  # a burst right after construction: too soon
        reporter(copied, 10 * MB)
    assert status.edits == []

    clock.t = 3.0
    reporter(5 * MB, 10 * MB)
    reporter(6 * MB, 10 * MB)  # interval not elapsed since the edit above
    await reporter.finish()

    assert status.edits == ["📤 Uploading song.mp3 …\n▓▓▓▓▓░░░░░ 50% (5.0 / 10.0 MB)"]


async def test_unchanged_percent_is_not_re_edited() -> None:
    clock = FakeClock()
    status = FakeStatus()
    reporter = UploadProgressReporter(status, "song.mp3", now=clock)

    clock.t = 3.0
    reporter(5 * MB, 10 * MB)
    await reporter.finish()
    clock.t = 6.0
    reporter(5 * MB, 10 * MB)  # interval elapsed, but still 50%
    await reporter.finish()
    assert len(status.edits) == 1

    reporter(7 * MB, 10 * MB)  # interval elapsed and percent moved
    await reporter.finish()
    assert len(status.edits) == 2
    assert "70%" in status.edits[-1]


async def test_only_one_edit_in_flight() -> None:
    clock = FakeClock()
    status = FakeStatus()
    status.gate = asyncio.Event()
    reporter = UploadProgressReporter(status, "song.mp3", now=clock)

    clock.t = 3.0
    reporter(3 * MB, 10 * MB)
    await asyncio.sleep(0)  # let the edit start and block on the gate
    clock.t = 6.0
    reporter(8 * MB, 10 * MB)  # due by time and percent, but an edit is in flight

    status.gate.set()
    await reporter.finish()
    assert status.edits == ["📤 Uploading song.mp3 …\n▓▓▓░░░░░░░ 30% (3.0 / 10.0 MB)"]


async def test_finish_waits_for_the_pending_edit() -> None:
    clock = FakeClock()
    status = FakeStatus()
    status.gate = asyncio.Event()
    reporter = UploadProgressReporter(status, "song.mp3", now=clock)

    clock.t = 3.0
    reporter(3 * MB, 10 * MB)
    finishing = asyncio.create_task(reporter.finish())
    await asyncio.sleep(0)
    assert not finishing.done()

    status.gate.set()
    await finishing
    assert len(status.edits) == 1


async def test_zero_total_never_edits() -> None:
    clock = FakeClock()
    status = FakeStatus()
    reporter = UploadProgressReporter(status, "song.mp3", now=clock)

    clock.t = 10.0
    reporter(5 * MB, 0)
    await reporter.finish()
    assert status.edits == []
