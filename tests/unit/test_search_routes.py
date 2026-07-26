"""Tests for the Add New page: it acquires songs and flags ones already held."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
import werkzeug
from flask import Flask
from flask_babel import Babel

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.routes.search import search_bp

PIKARAOKE = Path(__file__).resolve().parents[2] / "pikaraoke"

# (title, url, id, channel, duration) -- the shape get_search_results returns
HELD = ("Held Song", "https://youtu.be/aaaaaaaaaaa", "aaaaaaaaaaa", "Chan", "3:21")
MISSING = ("Missing Song", "https://youtu.be/zzzzzzzzzzz", "zzzzzzzzzzz", "Chan", "4:05")

# The rendered elements, not the bare class name -- that also appears in the page's CSS.
# The row carries the queue-or-not intent, so there is no global toggle to read.
QUEUE_BUTTON = 'class="button is-info search_result_items_download" data-queue="1"'
SAVE_BUTTON = 'class="button search_result_items_download" data-queue=""'


@pytest.fixture
def app():
    test_app = Flask(__name__, template_folder=str(PIKARAOKE / "templates"))
    Babel(test_app)
    test_app.register_blueprint(search_bp)
    for rule, endpoint in (
        ("/", "home.home"),
        ("/queue", "queue.queue"),
        ("/queue/enqueue", "queue.enqueue"),
        ("/browse", "files.browse"),
        ("/info", "info.info"),
    ):
        test_app.add_url_rule(rule, endpoint, lambda: "", methods=["GET"])

    @test_app.context_processor
    def inject_path_config():
        return {"base_path": "", "socketio_path": "/socket.io", "cookie_path": "/"}

    test_app.jinja_env.globals.update(url_escape=quote)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


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
        assert "song_to_add" not in body
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
        body = response.data.decode()
        assert "In library" in body
        assert "add-song-link" in body
        assert QUEUE_BUTTON not in body
        assert SAVE_BUTTON not in body

    def test_missing_song_offers_both_queue_and_save(self, client):
        """The row carries the intent, so there is no global queue-once-downloaded toggle."""
        response, _ = _search(client, [MISSING], {})
        body = response.data.decode()
        assert "In library" not in body
        assert QUEUE_BUTTON in body
        assert SAVE_BUTTON in body

    def test_no_global_queue_toggle_remains(self, client):
        """The removed checkboxes were #add-to-queue-direct and #add-to-queue-search."""
        response, _ = _search(client, [MISSING], {})
        assert "add-to-queue" not in response.data.decode()

    def test_a_mixed_page_renders_both(self, client):
        response, _ = _search(client, [HELD, MISSING], {"aaaaaaaaaaa": "/songs/Held Song.mp4"})
        body = response.data.decode()
        assert "In library" in body
        assert "add-song-link" in body
        assert QUEUE_BUTTON in body
        assert SAVE_BUTTON in body
