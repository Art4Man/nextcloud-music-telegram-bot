from nc_music_bot.naming import (
    build_filename,
    is_safe_remote_name,
    numbered_variant,
    sanitize_filename,
)


class TestSanitizeFilename:
    def test_plain_name_unchanged(self) -> None:
        assert sanitize_filename("My Song.mp3") == "My Song.mp3"

    def test_posix_traversal_stripped(self) -> None:
        assert sanitize_filename("../../etc/passwd") == "passwd"

    def test_windows_traversal_stripped(self) -> None:
        assert sanitize_filename("..\\..\\Windows\\evil.mp3") == "evil.mp3"

    def test_hidden_file_unhidden(self) -> None:
        assert sanitize_filename(".hidden.mp3") == "hidden.mp3"

    def test_only_dots_falls_back(self) -> None:
        assert sanitize_filename("...") == "audio"

    def test_empty_falls_back(self) -> None:
        assert sanitize_filename("") == "audio"

    def test_control_characters_removed(self) -> None:
        assert sanitize_filename("ba\x00d\nname.mp3") == "badname.mp3"

    def test_whitespace_collapsed(self) -> None:
        assert sanitize_filename("  My   Song.mp3  ") == "My Song.mp3"

    def test_unicode_preserved(self) -> None:
        assert sanitize_filename("Türkü – Şarkı.mp3") == "Türkü – Şarkı.mp3"  # noqa: RUF001

    def test_long_name_truncated_keeping_extension(self) -> None:
        result = sanitize_filename("x" * 300 + ".flac")
        assert len(result.encode()) <= 200
        assert result.endswith(".flac")

    def test_multibyte_truncation_counts_bytes(self) -> None:
        result = sanitize_filename("é" * 150 + ".mp3")
        assert len(result.encode()) <= 200
        assert result.endswith(".mp3")


class TestBuildFilename:
    def test_sent_name_wins(self) -> None:
        assert build_filename("../song.mp3", "A", "T", "audio/mpeg", "u1") == "song.mp3"

    def test_tags_compose_name(self) -> None:
        assert build_filename(None, "Artist", "Title", "audio/mpeg", "u1") == "Artist - Title.mp3"

    def test_title_only(self) -> None:
        assert build_filename(None, None, "Title", "audio/flac", "u1") == "Title.flac"

    def test_m4a_mime_mapped(self) -> None:
        assert build_filename(None, "A", "T", "audio/x-m4a", "u1") == "A - T.m4a"

    def test_no_metadata_uses_unique_id(self) -> None:
        assert build_filename(None, None, None, "audio/mpeg", "abc123") == "audio_abc123.mp3"

    def test_no_mime_no_extension(self) -> None:
        assert build_filename(None, None, None, None, "abc123") == "audio_abc123"


class TestRemoteNaming:
    def test_numbered_variant_keeps_extension(self) -> None:
        assert numbered_variant("song.mp3", 1) == "song (1).mp3"

    def test_numbered_variant_without_extension(self) -> None:
        assert numbered_variant("song", 2) == "song (2)"

    def test_numbered_variant_multiple_dots(self) -> None:
        assert numbered_variant("a.b.c.mp3", 3) == "a.b.c (3).mp3"

    def test_safe_names(self) -> None:
        assert is_safe_remote_name("song.mp3")
        assert is_safe_remote_name("Artist - Title.flac")

    def test_unsafe_names(self) -> None:
        assert not is_safe_remote_name("")
        assert not is_safe_remote_name(".")
        assert not is_safe_remote_name("..")
        assert not is_safe_remote_name(".hidden")
        assert not is_safe_remote_name("a/b.mp3")
        assert not is_safe_remote_name("a\\b.mp3")
