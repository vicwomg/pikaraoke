"""Tests for the browse route's filtering, sorting, and fragment rendering."""

import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest
import werkzeug
from flask import Flask
from flask_babel import Babel

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.lib.song_manager import SongManager
from pikaraoke.routes.files import files_bp

PIKARAOKE = Path(__file__).resolve().parents[2] / "pikaraoke"


def _stub_endpoints(app):
    """Minimal stand-ins for the endpoints base.html and files.html link to."""
    for rule, endpoint in (
        ("/", "home.home"),
        ("/queue", "queue.queue"),
        ("/queue/enqueue", "queue.enqueue"),
        ("/search", "search.search"),
        ("/info", "info.info"),
        ("/batch", "batch_song_renamer.browse"),
    ):
        app.add_url_rule(rule, endpoint, lambda: "", methods=["GET"])


@pytest.fixture
def app():
    test_app = Flask(__name__, template_folder=str(PIKARAOKE / "templates"))
    test_app.config["PIKARAOKE_BASE_PATH"] = ""
    test_app.config["PIKARAOKE_SOCKETIO_PATH"] = "/socket.io"
    Babel(test_app)
    test_app.register_blueprint(files_bp)
    _stub_endpoints(test_app)

    @test_app.context_processor
    def inject_path_config():
        return {"base_path": "", "socketio_path": "/socket.io", "cookie_path": "/"}

    test_app.jinja_env.globals.update(url_escape=quote)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


def _sort_links(body):
    """Map each sort button's data-sort value ("" for alphabetical) to its href."""
    return dict(re.findall(r'data-sort="([^"]*)"\s*href="([^"]+)"', body))


def _browse(client, app, songs, query="", per_page=100):
    """GET /browse against a karaoke stand-in whose song_manager is the real thing."""
    sm = SongManager("/songs", db=MagicMock(), get_title_tidy=lambda: False)
    sm.songs.update(songs)
    app.jinja_env.globals.update(filename_from_path=sm.display_name_from_path)
    k = MagicMock()
    k.song_manager = sm
    k.browse_results_per_page = per_page
    with (
        patch("pikaraoke.routes.files.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.files.get_site_name", return_value="PiKaraoke"),
        patch("pikaraoke.routes.files.is_admin", return_value=True),
    ):
        return client.get("/browse" + query)


class TestFilter:
    def test_q_narrows_the_list(self, client, app):
        songs = ["/songs/Abba - Waterloo.mp4", "/songs/Queen - Bohemian Rhapsody.mp4"]
        response = _browse(client, app, songs, "?q=waterloo")
        body = response.data.decode()
        assert "Abba - Waterloo" in body
        assert "Bohemian" not in body

    def test_q_searches_the_whole_library_not_just_the_current_page(self, client, app):
        """A client-side filter over the rendered rows would miss this one entirely."""
        songs = [f"/songs/Filler {i:03d}.mp4" for i in range(150)]
        songs.append("/songs/Zzz Needle In Haystack.mp4")
        response = _browse(client, app, songs, "?q=needle", per_page=100)
        body = response.data.decode()
        assert "Needle In Haystack" in body
        assert "Filler" not in body

    def test_q_overrides_letter(self, client, app):
        songs = ["/songs/Abba - Waterloo.mp4", "/songs/Queen - Bohemian Rhapsody.mp4"]
        response = _browse(client, app, songs, "?q=bohemian&letter=a")
        body = response.data.decode()
        assert "Bohemian" in body
        assert "Waterloo" not in body

    def test_filtered_count_and_page_links_are_rendered(self, client, app):
        """flask_paginate counts pages from `found` in search mode, not `total`."""
        songs = [f"/songs/Abba - Song {i}.mp4" for i in range(10)]
        body = _browse(client, app, songs, "?q=abba", per_page=2).data.decode()
        assert "<b>10</b> songs" in body
        assert "page=2" in body

    def test_blank_q_returns_everything(self, client, app):
        songs = ["/songs/Abba - Waterloo.mp4", "/songs/Queen - Bohemian Rhapsody.mp4"]
        body = _browse(client, app, songs, "?q=%20%20").data.decode()
        assert "Waterloo" in body
        assert "Bohemian" in body

    def test_letter_still_filters_without_q(self, client, app):
        songs = ["/songs/Abba - Waterloo.mp4", "/songs/Queen - Bohemian Rhapsody.mp4"]
        body = _browse(client, app, songs, "?letter=q").data.decode()
        assert "Bohemian" in body
        assert "Waterloo" not in body


class TestSortLinksPreserveTheFilter:
    """Sorting is orthogonal to filtering; only the alpha bar clears the query."""

    SONGS = ["/songs/Abba - Waterloo.mp4", "/songs/Queen - Bohemian Rhapsody.mp4"]

    def test_sort_by_date_link_carries_q(self, client, app):
        body = _browse(client, app, self.SONGS, "?q=abba").data.decode()
        link = _sort_links(body)["date"]
        assert "q=abba" in link
        assert "sort=date" in link

    def test_sort_by_name_link_carries_q(self, tmp_path, client, app):
        """sort=date stats the files, so this one needs real paths."""
        song = tmp_path / "Abba - Waterloo.mp4"
        song.write_text("fake")
        body = _browse(client, app, [str(song)], "?q=abba&sort=date").data.decode()
        link = _sort_links(body)[""]
        assert "q=abba" in link
        assert "sort=" not in link

    def test_sort_links_carry_letter_when_no_query(self, client, app):
        body = _browse(client, app, self.SONGS, "?letter=a").data.decode()
        links = _sort_links(body)
        assert set(links) == {"", "date"}
        assert all("letter=a" in href for href in links.values())

    def test_alpha_bar_links_stay_bare(self, client, app):
        """The A-Z bar is the one control that resets the filter."""
        body = _browse(client, app, self.SONGS, "?q=abba").data.decode()
        alpha = re.findall(r'href="([^"]*letter=[^"]*)"', body)
        assert alpha, "expected alpha-bar links"
        assert all("q=" not in href for href in alpha)


class TestFilterWithDateSort:
    def test_filter_applies_before_the_sort(self, tmp_path, client, app):
        """Ordering is what is load-bearing: the filtered set, newest first."""
        names = ["Abba - Waterloo.mp4", "Abba - Dancing Queen.mp4", "Queen - Bohemian.mp4"]
        paths = []
        for i, name in enumerate(names):
            p = tmp_path / name
            p.write_text("fake")
            os.utime(p, (1_600_000_000 + i * 100, 1_600_000_000 + i * 100))
            paths.append(str(p))

        body = _browse(client, app, paths, "?q=abba&sort=date").data.decode()
        assert "Bohemian" not in body
        # Dancing Queen is newer than Waterloo, so it must come first
        assert body.index("Dancing Queen") < body.index("Waterloo")


class TestPartialFragment:
    SONGS = ["/songs/Abba - Waterloo.mp4", "/songs/Queen - Bohemian Rhapsody.mp4"]

    def test_partial_returns_only_the_results(self, client, app):
        body = _browse(client, app, self.SONGS, "?q=abba&partial=1").data.decode()
        assert "Waterloo" in body
        assert "<html" not in body
        assert "<nav" not in body
        assert "song-filter" not in body

    def test_full_page_is_unchanged_by_the_partial_flag(self, client, app):
        body = _browse(client, app, self.SONGS, "?q=abba").data.decode()
        assert "<html" in body
        assert "song-filter" in body

    def test_partial_never_leaks_into_generated_urls(self, client, app):
        """Invisible until a user clicks page 2 of a filtered set and gets a bare table."""
        songs = [f"/songs/Abba - Song {i}.mp4" for i in range(10)]
        body = _browse(client, app, songs, "?q=abba&partial=1", per_page=2).data.decode()
        assert "page=" in body, "expected pagination links to assert against"
        assert "partial" not in body

    def test_partial_absent_from_the_edit_referrer(self, client, app):
        """current_url becomes the edit button's referrer; a fragment URL would break saving."""
        body = _browse(client, app, self.SONGS, "?q=abba&partial=1").data.decode()
        assert "referrer=" in body
        assert "partial" not in body
