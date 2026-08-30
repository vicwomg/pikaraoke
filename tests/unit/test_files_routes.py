"""Tests for the browse route's filtering, sorting, and fragment rendering."""

import contextlib
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import werkzeug

if not hasattr(werkzeug, "__version__"):
    werkzeug.__version__ = "3.0.0"

from pikaraoke.karaoke import SongInUseError
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.metadata_parser import sanitize_filename
from pikaraoke.lib.song_manager import SongManager
from pikaraoke.routes.files import files_bp
from tests.conftest import StubAdminAuth, make_route_app


@pytest.fixture
def app():
    app = make_route_app(
        files_bp,
        [
            ("/", "home.home"),
            ("/queue", "queue.queue"),
            ("/search", "search.search"),
            ("/info", "info.info"),
            ("/batch", "batch_song_renamer.browse"),
        ],
    )
    # The rename route flashes, and flashing writes to the session.
    app.secret_key = "test"
    return app


def _sort_links(body):
    """Map each sort button's data-sort value ("" for alphabetical) to its href."""
    return dict(re.findall(r'data-sort="([^"]*)"\s*href="([^"]+)"', body))


def _alpha_bar(body):
    """(linked, greyed) keys from the A-Z strip, or None when it is not rendered.

    Read from the strip's own markup rather than the whole page: sort and pager
    links carry `letter` too, so a page-wide search reports links that are not there.
    """
    match = re.search(r'<div id="alpha-bar".*?</div>', body, re.S)
    if not match:
        return None
    bar = match.group(0)
    linked = set(re.findall(r'letter=([a-z]+)[&"]', bar))
    greyed = {t.lower() for t in re.findall(r'<span class="alpha-empty">([^<]+)</span>', bar)}
    return linked, greyed


def _karaoke(app, songs, per_page, folders=True):
    """A karaoke stand-in whose song_manager is the real thing."""
    sm = SongManager("/songs", db=MagicMock(), events=EventSystem(), get_title_tidy=lambda: False)
    sm.songs.update(songs)
    app.jinja_env.globals.update(filename_from_path=sm.display_name_from_path)
    k = MagicMock()
    k.song_manager = sm
    k.browse_results_per_page = per_page
    # Set explicitly: a bare MagicMock attribute is truthy, so leaving it out would
    # silently enable folder browsing in every test and never exercise the gate.
    k.enable_folder_browsing = folders
    return k


def _browse(
    client, app, songs=(), query="", per_page=100, admin=True, cookie=None, k=None, folders=True
):
    """GET /browse. `per_page` is the server-wide default, `cookie` one device's choice."""
    k = k if k is not None else _karaoke(app, songs, per_page, folders)
    if cookie is not None:
        client.set_cookie("browse_per_page", cookie)
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin)
    with (
        patch("pikaraoke.routes.files.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.files.get_site_name", return_value="PiKaraoke"),
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
        """The pager counts the filtered set, not the library behind it."""
        songs = [f"/songs/Abba - Song {i}.mp4" for i in range(10)]
        body = _browse(client, app, songs, "?q=abba", per_page=2).data.decode()
        assert "1-2 of 10" in body
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


class TestPaginationLinksEscapeTheQuery:
    """Only the page number is un-escaped in the pagination href; the query is not."""

    @staticmethod
    def _page_links(body):
        return re.findall(r'href="([^"]*page=\d+[^"]*)"', body)

    def test_ampersand_in_q_does_not_split_the_link(self, client, app):
        songs = [f"/songs/Simon & Garfunkel - Song {i}.mp4" for i in range(10)]
        body = _browse(client, app, songs, "?q=Simon+%26+Garfunkel", per_page=2).data.decode()
        links = self._page_links(body)
        assert links, "expected pagination links to assert against"
        # Unescaped, "&" would end the q parameter and page 2 would search "Simon "
        assert all("q=Simon+%26+Garfunkel" in href for href in links)

    def test_hash_in_q_does_not_become_a_fragment(self, client, app):
        songs = [f"/songs/Rock #1 Hits {i}.mp4" for i in range(10)]
        body = _browse(client, app, songs, "?q=rock+%231", per_page=2).data.decode()
        links = self._page_links(body)
        assert links, "expected pagination links to assert against"
        # A bare "#" would make everything after it a fragment, so `page` would
        # never reach the server and every link would render page 1.
        assert all("#" not in href for href in links)


class TestPagerBounds:
    """The pager links to the page either side of this one, so `page` has to be real."""

    SONGS = [f"/songs/Song {i:02d}.mp4" for i in range(10)]

    def test_a_page_past_the_end_lands_on_the_last_one(self, client, app):
        """Deleting the last row, or filtering to a shorter list, strands the pager."""
        body = _browse(client, app, self.SONGS, "?page=99", per_page=4).data.decode()
        assert "9-10 of 10" in body
        assert "Song 08" in body

    def test_page_zero_does_not_index_from_the_wrong_end(self, client, app):
        body = _browse(client, app, self.SONGS, "?page=0", per_page=4).data.decode()
        assert "1-4 of 10" in body

    def test_a_junk_page_is_a_request_for_the_first_one(self, client, app):
        """A bookmark carrying junk should not be a 500."""
        response = _browse(client, app, self.SONGS, "?page=abc", per_page=4)
        assert response.status_code == 200
        assert "1-4 of 10" in response.data.decode()

    def test_an_empty_result_reports_no_rows(self, client, app):
        body = _browse(client, app, self.SONGS, "?q=zzz").data.decode()
        assert "0-0 of 0" in body

    def test_a_single_page_carries_one_pager_not_two(self, client, app):
        """Two stacked pagers around a short table are one control drawn twice."""
        body = _browse(client, app, self.SONGS, per_page=100).data.decode()
        assert body.count("pager-controls") == 1

    def test_a_paged_list_keeps_the_pager_at_both_ends(self, client, app):
        body = _browse(client, app, self.SONGS, per_page=4).data.decode()
        assert body.count("pager-controls") == 2


class TestPerPageControl:
    """The size belongs to the device, so everyone is offered the dropdown."""

    SONGS = [f"/songs/Song {i:02d}.mp4" for i in range(10)]

    def test_the_dropdown_is_offered_to_a_guest(self, client, app):
        """It was admin-only while the size was a server-wide preference."""
        body = _browse(client, app, self.SONGS, per_page=25, admin=False).data.decode()
        assert 'id="pager-per-page"' in body

    def test_the_size_in_force_is_always_selectable(self, client, app):
        """A value Settings allows but the list does not would otherwise be unshowable."""
        body = _browse(client, app, self.SONGS, per_page=70).data.decode()
        assert '<option value="70" selected>70</option>' in body

    def test_only_the_top_pager_carries_it(self, client, app):
        """Reaching the bottom of the page is the thing the control exists to avoid."""
        body = _browse(client, app, self.SONGS, per_page=25).data.decode()
        assert body.count('id="pager-per-page"') == 1


class TestPerPageCookie:
    """The chosen size is this device's own and must never move the shared default."""

    SONGS = [f"/songs/Song {i:02d}.mp4" for i in range(30)]

    def test_the_cookie_sizes_the_page(self, client, app):
        body = _browse(client, app, self.SONGS, per_page=100, cookie="20").data.decode()
        assert "1-20 of 30" in body

    def test_the_server_default_applies_without_a_cookie(self, client, app):
        body = _browse(client, app, self.SONGS, per_page=100).data.decode()
        assert "1-30 of 30" in body

    def test_browsing_never_writes_the_server_default(self, client, app):
        """The reported bug: picking a size here moved the default shown in Settings."""
        k = _karaoke(app, self.SONGS, per_page=100)
        _browse(client, app, cookie="20", k=k)
        k.preferences.set.assert_not_called()

    def test_the_size_outlives_admin(self, client, app):
        """Logging out is not a reason to lose a display choice."""
        body = _browse(
            client, app, self.SONGS, per_page=100, cookie="20", admin=False
        ).data.decode()
        assert "1-20 of 30" in body

    def test_junk_falls_back_to_the_default(self, client, app):
        response = _browse(client, app, self.SONGS, per_page=100, cookie="abc")
        assert response.status_code == 200
        assert "1-30 of 30" in response.data.decode()

    def test_a_size_off_the_menu_is_ignored(self, client, app):
        """A hand-edited cookie must not make the Pi render the whole library."""
        body = _browse(client, app, self.SONGS, per_page=100, cookie="50000").data.decode()
        assert "1-30 of 30" in body


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


def _patched(client, k, admin=True):
    """The gate and the route both read ADMIN_AUTH, so `admin` is set once."""
    client.application.config["ADMIN_AUTH"] = StubAdminAuth(admin)
    return (
        patch("pikaraoke.routes.files.get_karaoke_instance", return_value=k),
        patch("pikaraoke.routes.files.get_site_name", return_value="PiKaraoke"),
    )


def _post_rename(client, k, admin=True, **form):
    """POST /files/edit with the given form fields."""
    form = {"old_file_name": "/songs/Old.mp4", "new_file_name": "New", **form}
    with contextlib.ExitStack() as stack:
        for ctx in _patched(client, k, admin):
            stack.enter_context(ctx)
        return client.post("/files/edit", data=form)


def _get_edit(client, k, song, admin=True):
    with contextlib.ExitStack() as stack:
        for ctx in _patched(client, k, admin):
            stack.enter_context(ctx)
        return client.get(f"/files/edit?song={song}&referrer=/queue")


def _karaoke_for_rename(playing=None, queued=()):
    """A karaoke stand-in answering only what the rename route asks it."""
    k = MagicMock()
    k.playback_controller.now_playing_filename = playing
    k.queue_manager.is_song_in_queue.side_effect = lambda path: path in queued
    k.is_song_in_use.side_effect = lambda path: path == playing or path in queued
    k.db.get_format.return_value = None
    k.song_manager.filename_from_path.side_effect = lambda path, tidy=True: Path(path).stem
    k.song_manager.rename_target.side_effect = lambda path, name: str(
        Path(path).parent / (sanitize_filename(name) + Path(path).suffix)
    )
    return k


@pytest.fixture
def existing_song(tmp_path):
    song = tmp_path / "Old Name.mp4"
    song.write_text("fake")
    return song


class TestRenamePermission:
    def test_a_guest_renames_nothing(self, client, app):
        """The flash used to fire and the rename proceed anyway."""
        k = MagicMock()
        response = _post_rename(client, k, admin=False, referrer="/queue")
        assert response.status_code == 302
        assert response.headers["Location"] == "/"
        k.song_manager.rename.assert_not_called()  # pylint: disable=no-member


class TestRenameThePlayingSong:
    """On POSIX this renamed the file out from under FFmpeg and broke subtitles."""

    def test_the_claimed_song_has_no_edit_page(self, client, app):
        k = _karaoke_for_rename(playing="/songs/Old.mp4")
        response = _get_edit(client, k, "/songs/Old.mp4")
        assert response.status_code == 302
        assert response.headers["Location"] == "/queue"

    def test_a_queued_song_still_has_one(self, client, app):
        """The feature: the queue is no longer what blocks a rename."""
        k = _karaoke_for_rename(queued=["/songs/Old.mp4"])
        response = _get_edit(client, k, "/songs/Old.mp4")
        assert response.status_code == 200


class TestRenameFailuresKeepYourWork:
    """Only a rename that happened redirects; everything else re-renders."""

    def _submit(self, client, k, song, new_file_name="New Name"):
        return _post_rename(
            client,
            k,
            old_file_name=str(song),
            new_file_name=new_file_name,
            referrer="/queue",
        )

    def test_a_successful_rename_redirects_to_the_referrer(self, client, app, existing_song):
        k = _karaoke_for_rename()
        response = self._submit(client, k, existing_song)
        assert response.status_code == 302
        assert response.headers["Location"] == "/queue"
        k.rename_song.assert_called_once_with(str(existing_song), "New Name")

    def test_a_blank_name_is_rejected(self, client, app, existing_song):
        """It used to rename the song to '.mp4' and drop it out of the song list."""
        k = _karaoke_for_rename()
        response = self._submit(client, k, existing_song, new_file_name="   ")
        assert response.status_code == 200
        k.rename_song.assert_not_called()

    def test_a_vanished_source_is_reported_before_the_rename(self, client, app):
        k = _karaoke_for_rename()
        response = self._submit(client, k, "/songs/Gone.mp4")
        assert response.status_code == 200
        assert "no longer in the library" in response.data.decode()
        k.rename_song.assert_not_called()

    def test_a_name_collision_is_reported(self, client, app, existing_song):
        taken = existing_song.parent / "Taken.mp4"
        taken.write_text("fake")
        k = _karaoke_for_rename()
        response = self._submit(client, k, existing_song, new_file_name="Taken")
        assert response.status_code == 200
        assert "already exists" in response.data.decode()
        k.rename_song.assert_not_called()

    def test_a_collision_only_visible_after_sanitizing_is_reported(
        self, client, app, existing_song
    ):
        """'AC/DC' is written as 'AC-DC', which os.rename would have replaced in silence."""
        (existing_song.parent / "AC-DC - Thunderstruck.mp4").write_text("fake")
        k = _karaoke_for_rename()
        response = self._submit(client, k, existing_song, new_file_name="AC/DC - Thunderstruck")
        assert response.status_code == 200
        assert "already exists" in response.data.decode()
        k.rename_song.assert_not_called()

    def test_changing_only_the_case_is_not_a_collision(self, client, app, existing_song):
        """On NTFS and APFS the target 'exists' only because it is this very song."""
        k = _karaoke_for_rename()
        new_name = existing_song.stem.upper()
        response = self._submit(client, k, existing_song, new_file_name=new_name)
        assert response.status_code == 302
        k.rename_song.assert_called_once_with(str(existing_song), new_name)

    def test_saving_the_name_it_already_has_is_not_a_collision(self, client, app, existing_song):
        """A song that already fits the naming convention is done, not in conflict with itself."""
        k = _karaoke_for_rename()
        response = self._submit(client, k, existing_song, new_file_name=existing_song.stem)
        assert response.status_code == 302
        assert response.headers["Location"] == "/queue"
        k.rename_song.assert_not_called()

    def test_a_song_claimed_mid_edit_is_reported(self, client, app, existing_song):
        k = _karaoke_for_rename()
        k.rename_song.side_effect = SongInUseError(str(existing_song))
        response = self._submit(client, k, existing_song)
        assert response.status_code == 200
        assert "started playing while you were editing it" in response.data.decode()

    def test_an_os_error_is_reported(self, client, app, existing_song):
        k = _karaoke_for_rename()
        k.rename_song.side_effect = OSError("disk on fire")
        response = self._submit(client, k, existing_song)
        assert response.status_code == 200
        assert "disk on fire" in response.data.decode()

    def test_a_refusal_keeps_the_typed_name_and_the_referrer(self, client, app, existing_song):
        k = _karaoke_for_rename()
        k.rename_song.side_effect = SongInUseError(str(existing_song))
        response = self._submit(client, k, existing_song, new_file_name="Half Typed Name")
        body = response.data.decode()
        assert 'value="Half Typed Name"' in body
        assert 'value="/queue"' in body


# Nested library shared by the folder tests below. "Loose" sits at the top level,
# so it separates "every song" from "every song not in a subfolder".
NESTED = [
    "/songs/Loose Track.mp4",
    "/songs/Rock/Bon Jovi - Livin.mp4",
    "/songs/Rock/Metal/Heavy Song.mp4",
    "/songs/Pop/Abba - Waterloo.mp4",
]

# "Genres" holds a subfolder and no songs of its own.
SUBFOLDERS_ONLY = ["/songs/Genres/Rock/Heavy Song.mp4"]


class TestFolderView:
    def test_flat_view_lists_every_song_wherever_it_sits(self, client, app):
        """The default view is what most libraries use; folders must not disturb it."""
        body = _browse(client, app, NESTED).data.decode()
        assert "Loose Track" in body
        assert "Bon Jovi" in body
        assert "Heavy Song" in body

    def test_folder_root_lists_subfolders_and_only_loose_songs(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders").data.decode()
        assert "Rock" in body
        assert "Pop" in body
        assert "Loose Track" in body
        assert "Bon Jovi" not in body

    def test_entering_a_folder_lists_its_direct_songs_only(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock").data.decode()
        assert "Bon Jovi" in body
        assert "Loose Track" not in body
        # One level deeper: offered as a subfolder, not listed as a song.
        assert "Heavy Song" not in body
        assert "Metal" in body

    def test_nested_folder_is_reached_by_its_full_key(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock/Metal").data.decode()
        assert "Heavy Song" in body
        assert "Bon Jovi" not in body

    def test_breadcrumbs_appear_inside_a_folder(self, client, app):
        """The attribute, not the bare word: a .breadcrumb CSS rule ships on every page."""
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock/Metal").data.decode()
        assert 'class="breadcrumb' in body
        assert ">Rock<" in body and ">Metal<" in body

    def test_view_toggle_is_absent_for_a_flat_library(self, client, app):
        body = _browse(client, app, ["/songs/A.mp4", "/songs/B.mp4"]).data.decode()
        assert "view=folders" not in body

    def test_alpha_bar_stays_inside_a_folder(self, client, app):
        """It narrows the folder being browsed, so it belongs on the folder page."""
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock").data.decode()
        assert _alpha_bar(body) is not None

    def test_alpha_bar_is_dropped_for_a_folder_holding_only_subfolders(self, client, app):
        """No songs on the page to narrow, so all 27 keys would be dead."""
        body = _browse(client, app, SUBFOLDERS_ONLY, "?view=folders&folder=Genres").data.decode()
        assert _alpha_bar(body) is None


class TestAlphaBarFollowsTheScope:
    """A key that would return nothing is greyed rather than dropped, so the strip
    keeps the same shape in every folder."""

    def test_only_letters_held_directly_by_the_folder_are_linked(self, client, app):
        linked, greyed = _alpha_bar(
            _browse(client, app, NESTED, "?view=folders&folder=Rock").data.decode()
        )
        assert linked == {"b"}, "Bon Jovi is the only song sitting directly in Rock"
        assert "h" in greyed, "Heavy Song is in Rock/Metal, which Rock does not list"
        assert "a" in greyed, "Abba is in Pop"

    def test_the_flat_view_offers_every_letter_in_the_library(self, client, app):
        linked, greyed = _alpha_bar(_browse(client, app, NESTED).data.decode())
        assert {"a", "b", "h", "l"} <= linked
        assert "z" in greyed

    def test_the_strip_always_carries_all_27_keys(self, client, app):
        """Dropping the dead ones would resize the strip on every folder change."""
        linked, greyed = _alpha_bar(
            _browse(client, app, NESTED, "?view=folders&folder=Rock").data.decode()
        )
        assert len(linked | greyed) == 27

    def test_a_digit_lights_the_hash(self, client, app):
        linked, _ = _alpha_bar(_browse(client, app, ["/songs/99 Luftballons.mp4"]).data.decode())
        assert "numeric" in linked

    def test_a_dead_letter_is_not_a_link(self, client, app):
        """Greying it in CSS alone would still leave 25 tab stops before the list."""
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock").data.decode()
        bar = re.search(r'<div id="alpha-bar".*?</div>', body, re.S).group(0)
        assert "letter=z" not in bar

    def test_an_active_letter_matching_nothing_keeps_the_strip(self, client, app):
        """Otherwise the control that got you here vanishes and there is no way back."""
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock&letter=z").data.decode()
        assert _alpha_bar(body) is not None


class TestFolderBrowsingIsOptIn:
    """Off by default: the Songs page must look exactly as it did without the feature."""

    def test_no_toggle_when_disabled(self, client, app):
        body = _browse(client, app, NESTED, folders=False).data.decode()
        assert "view=folders" not in body

    def test_every_song_still_listed_when_disabled(self, client, app):
        body = _browse(client, app, NESTED, folders=False).data.decode()
        assert "Loose Track" in body
        assert "Bon Jovi" in body
        assert "Heavy Song" in body

    def test_bookmarked_folder_url_falls_back_to_the_flat_list(self, client, app):
        """The preference can be turned off while a phone is sitting on a folder page."""
        body = _browse(
            client, app, NESTED, "?view=folders&folder=Rock", folders=False
        ).data.decode()
        assert "Heavy Song" in body
        assert 'class="breadcrumb' not in body

    def test_alpha_bar_returns_when_disabled(self, client, app):
        """The folder view's empty-scope rule must not leak into the flat page."""
        body = _browse(client, app, NESTED, "?view=folders", folders=False).data.decode()
        assert 'id="alpha-bar"' in body


class TestTraversalGuard:
    """Folder keys come from real song paths, so anything else clamps to the root."""

    def test_dot_dot_traversal_clamps_to_the_root(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=../../etc").data.decode()
        assert "Loose Track" in body
        assert 'class="breadcrumb' not in body

    def test_unknown_folder_clamps_to_the_root(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=Nope").data.decode()
        assert "Loose Track" in body
        assert 'class="breadcrumb' not in body

    def test_clamped_folder_is_dropped_from_generated_urls(self, client, app):
        """current_url becomes the edit referrer; it must describe what was rendered."""
        body = _browse(client, app, NESTED, "?view=folders&folder=Nope").data.decode()
        assert "Nope" not in body


class TestFolderScopeSurvivesTheControls:
    """Every URL the page generates has to keep the folder, or a click silently leaves it."""

    def test_sort_links_carry_the_folder(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock").data.decode()
        links = _sort_links(body)
        assert links, "expected sort links to assert against"
        assert all("view=folders" in href and "folder=Rock" in href for href in links.values())

    def test_alpha_bar_links_carry_the_folder(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=Rock&letter=b").data.decode()
        alpha = re.findall(r'href="([^"]*letter=[^"]*)"', body)
        assert alpha, "expected alpha-bar links"
        assert all("view=folders" in href and "folder=Rock" in href for href in alpha)

    def test_pagination_links_carry_the_folder(self, client, app):
        songs = [f"/songs/Rock/Song {i:03d}.mp4" for i in range(10)]
        body = _browse(client, app, songs, "?view=folders&folder=Rock", per_page=2).data.decode()
        links = re.findall(r'href="([^"]*page=\d+[^"]*)"', body)
        assert links, "expected pagination links"
        assert all("view=folders" in href and "folder=Rock" in href for href in links)

    def test_filtering_stays_inside_the_folder(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=Pop&q=a").data.decode()
        assert "Abba - Waterloo" in body
        assert "Bon Jovi" not in body

    def test_partial_fragment_stays_inside_the_folder(self, client, app):
        body = _browse(client, app, NESTED, "?view=folders&folder=Pop&partial=1").data.decode()
        assert "Abba - Waterloo" in body
        assert "Bon Jovi" not in body


class TestFolderLanding:
    """A folder holding only subfolders is navigation, not a result set."""

    ONLY_FOLDERS = ["/songs/Rock/a.mp4", "/songs/Pop/b.mp4"]

    def test_controls_and_table_are_hidden(self, client, app):
        """Attributes, not bare ids: the script block names all three regardless."""
        body = _browse(client, app, self.ONLY_FOLDERS, "?view=folders").data.decode()
        assert "Rock" in body
        assert 'id="song-filter"' not in body
        assert 'id="browse-results"' not in body
        assert 'class="button sort-link' not in body

    def test_an_active_query_keeps_the_controls(self, client, app):
        """A query matching nothing still has to be clearable."""
        body = _browse(client, app, self.ONLY_FOLDERS, "?view=folders&q=zzz").data.decode()
        assert 'id="song-filter"' in body
        assert 'id="browse-results"' in body
