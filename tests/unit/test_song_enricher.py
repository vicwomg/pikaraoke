"""Unit tests for the song enricher (iTunes + MusicBrainz pipeline)."""

from unittest.mock import patch

import pytest

from pikaraoke.lib import song_enricher
from pikaraoke.lib.karaoke_database import KaraokeDatabase


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


def _insert_song(db, path="/songs/Artist - Song---abc12345678.mp4"):
    db.insert_songs([{"file_path": path, "youtube_id": "abc12345678", "format": "mp4"}])
    return db.get_song_id_by_path(path)


def _row_with(**fields):
    """Mimic a sqlite3.Row (supports __getitem__ by column name)."""
    return fields


class TestQueryFromSong:
    def test_prefers_db_artist_and_title(self, tmp_path):
        row = _row_with(artist="Eminem", title="Stan")
        song = tmp_path / "Foo---abc12345678.mp4"
        assert song_enricher._query_from_song(row, str(song)) == "Eminem - Stan"

    def test_falls_back_to_stem_when_db_missing_fields(self, tmp_path):
        # Empty / None artist + title -> filename stem with YT id stripped.
        row = _row_with(artist=None, title="")
        song = tmp_path / "Queen - Bohemian Rhapsody---abc12345678.mp4"
        assert song_enricher._query_from_song(row, str(song)) == "Queen - Bohemian Rhapsody"

    def test_falls_back_to_stem_when_row_is_none(self, tmp_path):
        song = tmp_path / "Artist - Song---dQw4w9WgXcQ.mp4"
        assert song_enricher._query_from_song(None, str(song)) == "Artist - Song"

    def test_handles_bracket_youtube_id(self, tmp_path):
        song = tmp_path / "Artist - Song [dQw4w9WgXcQ].mp4"
        assert song_enricher._query_from_song(None, str(song)) == "Artist - Song"


class TestEnrichSong:
    def test_populates_nullable_fields_from_itunes(self, db, tmp_path):
        song_path = str(tmp_path / "Eminem - Stan---abc12345678.mp4")
        sid = _insert_song(db, song_path)

        itunes_full = {
            "itunes_id": "99999",
            "artist": "Eminem",
            "track": "Stan",
            "album": "The Marshall Mathers LP",
            "track_number": 3,
            "release_date": "2000-05-23T07:00:00Z",
            "cover_art_url": "https://fake/art.jpg",
            "genre": "Hip-Hop/Rap",
        }
        with patch.object(
            song_enricher, "fetch_itunes_track", return_value=itunes_full
        ), patch.object(song_enricher, "fetch_musicbrainz_ids", return_value=None), patch.object(
            song_enricher, "_download_cover", return_value=False
        ):
            song_enricher.enrich_song(db, sid, song_path)

        row = db.get_song_by_id(sid)
        assert row["itunes_id"] == "99999"
        assert row["artist"] == "Eminem"
        assert row["title"] == "Stan"
        assert row["album"] == "The Marshall Mathers LP"
        assert row["track_number"] == 3
        assert row["release_date"] == "2000-05-23T07:00:00Z"
        assert row["genre"] == "Hip-Hop/Rap"
        assert row["metadata_status"] == "enriched"
        assert row["enrichment_attempts"] == 1

    def test_manual_edits_beat_itunes(self, db, tmp_path):
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        # Pre-existing manual artist/title/album — provenance "manual" is
        # the top of the ladder so iTunes must not clobber.
        db.update_track_metadata_with_provenance(
            sid,
            "manual",
            {"artist": "Manual", "title": "Preset", "album": "Pre-album"},
        )

        itunes_full = {
            "itunes_id": "12345",
            "artist": "iTunes Artist",
            "track": "iTunes Track",
            "album": "iTunes Album",
            "track_number": 7,
            "release_date": None,
            "cover_art_url": None,
            "genre": None,
        }
        with patch.object(
            song_enricher, "fetch_itunes_track", return_value=itunes_full
        ), patch.object(song_enricher, "fetch_musicbrainz_ids", return_value=None):
            song_enricher.enrich_song(db, sid, song_path)

        row = db.get_song_by_id(sid)
        # Manual values preserved.
        assert row["artist"] == "Manual"
        assert row["title"] == "Preset"
        assert row["album"] == "Pre-album"
        # Unclaimed fields filled.
        assert row["itunes_id"] == "12345"
        assert row["track_number"] == 7

    def test_itunes_overwrites_youtube_sourced_fields(self, db, tmp_path):
        """iTunes (conf 2) beats YouTube info.json (conf 1) for identity fields."""
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        db.update_track_metadata_with_provenance(
            sid, "youtube", {"artist": "YouTube Artist", "title": "YouTube Track"}
        )

        itunes_full = {
            "itunes_id": "12345",
            "artist": "iTunes Artist",
            "track": "iTunes Track",
            "album": None,
            "track_number": None,
            "release_date": None,
            "cover_art_url": None,
            "genre": None,
        }
        with patch.object(
            song_enricher, "fetch_itunes_track", return_value=itunes_full
        ), patch.object(song_enricher, "fetch_musicbrainz_ids", return_value=None):
            song_enricher.enrich_song(db, sid, song_path)

        row = db.get_song_by_id(sid)
        assert row["artist"] == "iTunes Artist"
        assert row["title"] == "iTunes Track"
        sources = db.get_metadata_sources(sid)
        assert sources["artist"] == "itunes"
        assert sources["title"] == "itunes"

    def test_writes_musicbrainz_ids_when_available(self, db, tmp_path):
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        with patch.object(
            song_enricher,
            "fetch_itunes_track",
            return_value={
                "itunes_id": "1",
                "artist": "A",
                "track": "T",
                "album": None,
                "track_number": None,
                "release_date": None,
                "cover_art_url": None,
                "genre": None,
            },
        ), patch.object(
            song_enricher,
            "fetch_musicbrainz_ids",
            return_value={"musicbrainz_recording_id": "mbid-uuid", "isrc": "USRC17600001"},
        ):
            song_enricher.enrich_song(db, sid, song_path)

        row = db.get_song_by_id(sid)
        assert row["musicbrainz_recording_id"] == "mbid-uuid"
        assert row["isrc"] == "USRC17600001"

    def test_records_not_found_when_itunes_miss(self, db, tmp_path):
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        with patch.object(song_enricher, "fetch_itunes_track", return_value=None):
            song_enricher.enrich_song(db, sid, song_path)
        row = db.get_song_by_id(sid)
        assert row["metadata_status"] == "not_found"
        assert row["enrichment_attempts"] == 1

    def test_increments_attempts_on_repeated_runs(self, db, tmp_path):
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        with patch.object(song_enricher, "fetch_itunes_track", return_value=None):
            song_enricher.enrich_song(db, sid, song_path)
            song_enricher.enrich_song(db, sid, song_path)
            song_enricher.enrich_song(db, sid, song_path)
        row = db.get_song_by_id(sid)
        assert row["enrichment_attempts"] == 3

    def test_downloads_cover_and_registers_artifact(self, db, tmp_path):
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        itunes_full = {
            "itunes_id": "1",
            "artist": "A",
            "track": "T",
            "album": None,
            "track_number": None,
            "release_date": None,
            "cover_art_url": "https://fake/big.jpg",
            "genre": None,
        }
        expected_cover = str(tmp_path / "Foo---abc12345678.cover.jpg")

        def fake_download(url, dest):
            assert url == "https://fake/big.jpg"
            assert dest == expected_cover
            with open(dest, "wb") as f:
                f.write(b"image-bytes")
            return True

        with patch.object(
            song_enricher, "fetch_itunes_track", return_value=itunes_full
        ), patch.object(song_enricher, "fetch_musicbrainz_ids", return_value=None), patch.object(
            song_enricher, "_download_cover", side_effect=fake_download
        ):
            song_enricher.enrich_song(db, sid, song_path)

        arts = {(a["role"], a["path"]) for a in db.get_artifacts(sid)}
        assert ("cover_art", expected_cover) in arts

    def test_skips_cover_download_when_file_exists(self, db, tmp_path):
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        existing_cover = tmp_path / "Foo---abc12345678.cover.jpg"
        existing_cover.write_bytes(b"already-there")

        with patch.object(
            song_enricher,
            "fetch_itunes_track",
            return_value={
                "itunes_id": "1",
                "artist": "A",
                "track": "T",
                "album": None,
                "track_number": None,
                "release_date": None,
                "cover_art_url": "https://fake/big.jpg",
                "genre": None,
            },
        ), patch.object(song_enricher, "fetch_musicbrainz_ids", return_value=None), patch.object(
            song_enricher, "_download_cover"
        ) as mock_dl:
            song_enricher.enrich_song(db, sid, song_path)
            mock_dl.assert_not_called()

        # Existing file preserved.
        assert existing_cover.read_bytes() == b"already-there"

    def test_itunes_variant_does_not_override_title(self, db, tmp_path):
        """When iTunes' only hit is an Instrumental/Karaoke cut (a common
        small-catalogue edge case), its canonical artist/track must not
        clobber the existing row — otherwise LRCLib queries get poisoned
        with a suffix that doesn't exist in its index. Other iTunes
        fields (album, itunes_id, etc.) still flow through.
        """
        song_path = str(tmp_path / "Artist - Antyczny Napaleniec---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        db.update_track_metadata_with_provenance(
            sid,
            "youtube",
            {"artist": "Artist", "title": "Antyczny Napaleniec"},
        )
        itunes_full = {
            "itunes_id": "55555",
            "artist": "Artist",
            "track": "Antyczny Napaleniec (Instrumental)",
            "album": "Album Name",
            "track_number": 4,
            "release_date": "2020-01-01T00:00:00Z",
            "cover_art_url": None,
            "genre": "Rap",
        }
        with patch.object(
            song_enricher, "fetch_itunes_track", return_value=itunes_full
        ), patch.object(song_enricher, "fetch_musicbrainz_ids", return_value=None):
            song_enricher.enrich_song(db, sid, song_path)

        row = db.get_song_by_id(sid)
        # Identity fields stay at whatever was there (YouTube-seeded).
        assert row["title"] == "Antyczny Napaleniec"
        assert row["artist"] == "Artist"
        # Other iTunes-only fields still land.
        assert row["itunes_id"] == "55555"
        assert row["album"] == "Album Name"
        assert row["track_number"] == 4
        assert row["genre"] == "Rap"

    def test_itunes_variant_matching_query_still_applies(self, db, tmp_path):
        """When the query itself carries the variant suffix (user really
        wants the karaoke cut), iTunes' matching suffix is legitimate and
        must still flow through."""
        song_path = str(tmp_path / "Artist - Song (Instrumental)---abc12345678.mp4")
        sid = _insert_song(db, song_path)
        db.update_track_metadata_with_provenance(
            sid, "youtube", {"artist": "Artist", "title": "Song (Instrumental)"}
        )
        itunes_full = {
            "itunes_id": "1",
            "artist": "Artist Canonical",
            "track": "Song (Instrumental)",
            "album": None,
            "track_number": None,
            "release_date": None,
            "cover_art_url": None,
            "genre": None,
        }
        with patch.object(
            song_enricher, "fetch_itunes_track", return_value=itunes_full
        ), patch.object(song_enricher, "fetch_musicbrainz_ids", return_value=None):
            song_enricher.enrich_song(db, sid, song_path)

        row = db.get_song_by_id(sid)
        assert row["artist"] == "Artist Canonical"
        assert row["title"] == "Song (Instrumental)"

    def test_survives_provider_crashes(self, db, tmp_path):
        song_path = str(tmp_path / "Foo---abc12345678.mp4")
        sid = _insert_song(db, song_path)

        def boom_itunes(_):
            raise RuntimeError("iTunes is on fire")

        with patch.object(song_enricher, "fetch_itunes_track", side_effect=boom_itunes):
            song_enricher.enrich_song(db, sid, song_path)  # must not raise
        row = db.get_song_by_id(sid)
        assert row["metadata_status"] == "not_found"
        assert row["enrichment_attempts"] == 1
        assert row["last_enrichment_attempt"] is not None
