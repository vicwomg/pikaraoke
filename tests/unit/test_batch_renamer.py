"""Unit tests for batch_song_renamer route-level logic."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pikaraoke.karaoke import SongInUseError
from pikaraoke.lib.metadata_parser import sanitize_filename
from pikaraoke.routes.batch_song_renamer import _names_match, batch_song_renamer_bp
from tests.conftest import make_route_app


@pytest.fixture
def app():
    return make_route_app(batch_song_renamer_bp, [("/browse", "files.browse")])


@pytest.fixture
def client(app):
    return app.test_client()


def _renamed(path, name):
    return str(Path(path).parent / (sanitize_filename(name) + Path(path).suffix))


def _karaoke_for_rename(in_use=()):
    """A karaoke stand-in answering only what the rename route asks it."""
    k = MagicMock()
    k.is_song_in_use.side_effect = lambda path: path in in_use
    # Explicitly false: the queue is what the route used to ask, and it does
    # not hold the song being played. Only is_song_in_use may refuse here.
    k.queue_manager.is_song_in_queue.return_value = False
    k.song_manager.rename_target.side_effect = _renamed
    k.rename_song.side_effect = _renamed
    return k


def _accept(client, k, old_name, new_name):
    """POST the accept button's form, as the page's jQuery does."""
    with patch("pikaraoke.routes.batch_song_renamer.get_karaoke_instance", return_value=k):
        response = client.post(
            "/api/batch-song-renamer/rename-song",
            data={"old_name": old_name, "new_name": new_name},
        )
    return response.get_json()


class TestNamesMatch:
    """Tests for the _names_match comparison function."""

    def test_identical_names_match(self):
        assert _names_match("Artist - Song", "Artist - Song") is True

    def test_case_insensitive(self):
        assert _names_match("artist - song", "Artist - Song") is True

    def test_dash_variants_match(self):
        assert _names_match("Artist \u2013 Song", "Artist - Song") is True

    def test_whitespace_normalized(self):
        assert _names_match("Artist  -  Song", "Artist - Song") is True

    def test_accent_insensitive(self):
        assert _names_match("C\u00e9line Dion - Song", "Celine Dion - Song") is True

    def test_none_correct_name(self):
        assert _names_match("Artist - Song", None) is False

    def test_different_names(self):
        assert _names_match("Artist - Song A", "Artist - Song B") is False

    def test_empty_strings(self):
        assert _names_match("", "") is True


class TestAcceptingASuggestion:
    """The accept button renames through the same path as the edit page."""

    def test_the_youtube_id_survives(self, client, tmp_path):
        """Dropping it costs re-download dedup and play-history identity, unrecoverably."""
        song = str(tmp_path / "Whatever (Official Video)---dQw4w9WgXcQ.mp4")
        k = _karaoke_for_rename()

        body = _accept(client, k, song, "Rick Astley - Never Gonna Give You Up")

        assert body["success"] is True
        k.rename_song.assert_called_once_with(
            song, "Rick Astley - Never Gonna Give You Up---dQw4w9WgXcQ"
        )

    def test_the_song_stays_in_its_subfolder(self, client, tmp_path):
        """It used to be rebuilt under download_path, so the reply named a path that was not there."""
        folder = tmp_path / "Pop"
        folder.mkdir()
        song = str(folder / "Old---dQw4w9WgXcQ.mp4")
        k = _karaoke_for_rename()

        body = _accept(client, k, song, "New Name")

        assert Path(body["new_file_name"]).parent == folder

    def test_a_playing_song_is_refused(self, client, tmp_path):
        """is_song_in_queue misses it: the queue no longer holds the song being played."""
        song = str(tmp_path / "Old---dQw4w9WgXcQ.mp4")
        k = _karaoke_for_rename(in_use=(song,))

        body = _accept(client, k, song, "New Name")

        assert body["success"] is False
        k.rename_song.assert_not_called()

    def test_a_name_collision_is_refused(self, client, tmp_path):
        song = str(tmp_path / "Old---dQw4w9WgXcQ.mp4")
        Path(song).write_text("fake")
        (tmp_path / "Taken---dQw4w9WgXcQ.mp4").write_text("fake")
        k = _karaoke_for_rename()

        body = _accept(client, k, song, "Taken")

        assert body["success"] is False
        assert "already exists" in body["message"]
        k.rename_song.assert_not_called()

    def test_a_song_that_starts_playing_mid_rename_is_reported(self, client, tmp_path):
        song = str(tmp_path / "Old---dQw4w9WgXcQ.mp4")
        k = _karaoke_for_rename()
        k.rename_song.side_effect = SongInUseError(song)

        body = _accept(client, k, song, "New Name")

        assert body["success"] is False

    def test_a_filesystem_failure_is_reported_not_raised(self, client, tmp_path):
        """A restricted charset mount raises on a non-ASCII name; it used to 500."""
        song = str(tmp_path / "Old---dQw4w9WgXcQ.mp4")
        k = _karaoke_for_rename()
        k.rename_song.side_effect = OSError("Invalid argument")

        body = _accept(client, k, song, "Céline Dion - Pour que tu m'aimes encore")

        assert body["success"] is False
        assert "Invalid argument" in body["message"]
