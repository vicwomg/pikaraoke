"""Tests for the Add New page: it acquires songs and flags ones already held."""

from unittest.mock import MagicMock, patch

import pytest
import werkzeug

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.lib.youtube_dl import SearchResult
from pikaraoke.routes.search import search_bp
from tests.conftest import make_route_app

HELD = SearchResult("Held Song", "https://youtu.be/aaaaaaaaaaa", "aaaaaaaaaaa", "Chan", "3:21")
MISSING = SearchResult(
    "Missing Song", "https://youtu.be/zzzzzzzzzzz", "zzzzzzzzzzz", "Chan", "4:05"
)

# The rendered elements, not the bare class name -- that also appears in the page's CSS.
# The row carries the queue-or-not intent, so there is no global toggle to read.
QUEUE_BUTTON = 'class="button is-info search_result_items_download" data-queue="1"'
SAVE_BUTTON = 'class="button search_result_items_download" data-queue=""'


@pytest.fixture
def app():
    return make_route_app(
        search_bp,
        [
            ("/", "home.home"),
            ("/queue", "queue.queue"),
            ("/browse", "files.browse"),
            ("/info", "info.info"),
        ],
    )


def _rows(response):
    """The rendered result rows only.

    The page's script block builds the in-library markup client-side for songs
    that finish downloading, so a whole-body search matches text that is not on
    screen.
    """
    body = response.data.decode()
    start = body.index('<ul id="search-results"')
    return body[start : body.index("</ul>", start)]


def _search(client, results, library_matches):
    k = MagicMock()
    k.db.get_paths_by_youtube_ids.return_value = library_matches
    with (
        patch("pikaraoke.routes.search.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.search.get_site_name", return_value="PiKaraoke"),
        patch("pikaraoke.routes.search.get_search_results", return_value=results),
    ):
        return client.get("/search?search_string=whatever"), k


class TestAcquisitionOnly:
    def test_page_renders_without_the_local_library_dropdown(self, client):
        response, _ = _search(client, [MISSING], {})
        body = response.data.decode()
        assert response.status_code == 200
        assert "selectize" not in body
        # The removed dropdown was the only element naming this field; the shared
        # queueing script in base.html posts it, so the bare name now matches that.
        assert 'id="song_to_add"' not in body
        assert "search_string_input" in body

    def test_autocomplete_endpoint_is_gone(self, client):
        assert client.get("/autocomplete?q=abba").status_code == 404


class TestLibraryMatches:
    def test_looked_up_once_for_the_whole_page(self, client):
        _, k = _search(client, [HELD, MISSING], {})
        k.db.get_paths_by_youtube_ids.assert_called_once_with(["aaaaaaaaaaa", "zzzzzzzzzzz"])

    def test_not_queried_when_there_are_no_results(self, client):
        with (
            patch("pikaraoke.routes.search.get_karaoke_instance") as get_k,
            patch("pikaraoke.routes.search.get_site_name", return_value="PiKaraoke"),
        ):
            client.get("/search")
            get_k.return_value.db.get_paths_by_youtube_ids.assert_not_called()

    def test_held_song_offers_a_queue_action_instead_of_a_download(self, client):
        response, _ = _search(client, [HELD], {"aaaaaaaaaaa": "/songs/Held Song.mp4"})
        rows = _rows(response)
        assert "In library" in rows
        assert "add-song-link" in rows
        assert QUEUE_BUTTON not in rows
        assert SAVE_BUTTON not in rows

    def test_missing_song_offers_both_queue_and_save(self, client):
        """The row carries the intent, so there is no global queue-once-downloaded toggle."""
        response, _ = _search(client, [MISSING], {})
        rows = _rows(response)
        assert "In library" not in rows
        assert QUEUE_BUTTON in rows
        assert SAVE_BUTTON in rows

    def test_no_global_queue_toggle_remains(self, client):
        """The removed checkboxes were #add-to-queue-direct and #add-to-queue-search."""
        response, _ = _search(client, [MISSING], {})
        assert "add-to-queue" not in response.data.decode()

    def test_a_mixed_page_renders_both(self, client):
        response, _ = _search(client, [HELD, MISSING], {"aaaaaaaaaaa": "/songs/Held Song.mp4"})
        rows = _rows(response)
        assert "In library" in rows
        assert "add-song-link" in rows
        assert QUEUE_BUTTON in rows
        assert SAVE_BUTTON in rows

    def test_every_result_field_reaches_the_row(self, client):
        """The download script reads value= and data-ytTitle, so a dropped field
        breaks downloading at click time on a page that still looks correct."""
        response, _ = _search(client, [MISSING], {})
        rows = _rows(response)
        assert 'data-ytTitle="Missing Song"' in rows
        assert 'value="https://youtu.be/zzzzzzzzzzz"' in rows
        assert "vi/zzzzzzzzzzz/mqdefault.jpg" in rows
        assert "4:05" in rows
        assert "Chan" in rows
