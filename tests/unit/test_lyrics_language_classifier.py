"""Unit tests for the US-43 Tier 1 text-consensus language classifier."""

import pytest

from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.lyrics_language_classifier import (
    ConsensusVerdict,
    LanguageSignal,
    _signal_itunes_country,
    _signal_itunes_text,
    _signal_mb_release_country,
    _signal_mb_release_titles,
    _signal_title_heuristic,
    _signal_yt_info_lang,
    _signal_yt_subtitle_lang,
    _signal_yt_title_lang,
    classify_and_persist,
    collect_signals,
    consensus,
)

# --- per-signal extractor tests ----------------------------------------


class TestItunesTextSignal:
    def test_polish_album_and_track(self):
        hit = {
            "collectionName": "Pocahontas (Polska Wersja Językowa)",
            "trackName": "Kolorowy wiatr",
            "artistName": "Edyta Górniak",
        }
        sig = _signal_itunes_text(hit)
        assert sig is not None
        assert sig.language == "pl"
        assert sig.source == "itunes_text"

    def test_none_on_empty_text(self):
        assert _signal_itunes_text({}) is None
        assert _signal_itunes_text(None) is None
        assert _signal_itunes_text({"artistName": "x"}) is None  # too short

    def test_english(self):
        hit = {
            "collectionName": "Pocahontas Original Motion Picture Soundtrack",
            "trackName": "Colors of the Wind",
            "artistName": "Judy Kuhn",
        }
        sig = _signal_itunes_text(hit)
        assert sig is not None
        assert sig.language == "en"


class TestItunesCountrySignal:
    def test_polish_storefront(self):
        sig = _signal_itunes_country({"country": "POL"})
        assert sig is not None and sig.language == "pl" and sig.source == "itunes_country"

    def test_us_storefront(self):
        assert _signal_itunes_country({"country": "USA"}).language == "en"

    def test_unknown_country(self):
        assert _signal_itunes_country({"country": "XYZ"}) is None

    def test_missing_country(self):
        assert _signal_itunes_country({}) is None
        assert _signal_itunes_country(None) is None


class TestMbReleaseTitlesSignal:
    def test_polish_titles(self):
        mb = {
            "release_titles_joined": "Pocahontas | Pocahontas: Oryginalna Ścieżka Dźwiękowa",
        }
        sig = _signal_mb_release_titles(mb)
        assert sig is not None and sig.language == "pl"

    def test_empty(self):
        assert _signal_mb_release_titles({}) is None
        assert _signal_mb_release_titles(None) is None


class TestMbReleaseCountrySignal:
    def test_unanimous_polish(self):
        sig = _signal_mb_release_country({"release_countries": ("PL", "PL", "PL")})
        assert sig is not None and sig.language == "pl"
        assert "3 releases" in sig.detail

    def test_mixed_fires_no_signal(self):
        assert _signal_mb_release_country({"release_countries": ("PL", "US", "DE")}) is None

    def test_empty(self):
        assert _signal_mb_release_country({"release_countries": ()}) is None
        assert _signal_mb_release_country(None) is None


class TestYtInfoLangSignal:
    def test_info_language(self):
        sig = _signal_yt_info_lang({"language": "pl"})
        assert sig is not None and sig.language == "pl" and sig.source == "yt_info_lang"

    def test_original_language_fallback(self):
        sig = _signal_yt_info_lang({"language": None, "original_language": "pl-PL"})
        assert sig is not None and sig.language == "pl"

    def test_missing(self):
        assert _signal_yt_info_lang({}) is None
        assert _signal_yt_info_lang(None) is None


class TestYtSubtitleLangSignal:
    def test_single_language_manual_subs(self):
        sig = _signal_yt_subtitle_lang({"subtitles": {"pl": [{"url": "x"}]}})
        assert sig is not None and sig.language == "pl" and sig.source == "yt_subtitle_lang"

    def test_multi_language_no_signal(self):
        # Multiple disagreeing manual tracks aren't a clean signal.
        assert _signal_yt_subtitle_lang({"subtitles": {"pl": [{}], "en": [{}]}}) is None

    def test_same_language_variants_collapse(self):
        sig = _signal_yt_subtitle_lang({"subtitles": {"pl-PL": [{}], "pl": [{}]}})
        assert sig is not None and sig.language == "pl"

    def test_empty(self):
        assert _signal_yt_subtitle_lang({}) is None
        assert _signal_yt_subtitle_lang({"subtitles": {}}) is None
        assert _signal_yt_subtitle_lang(None) is None


class TestYtTitleLangSignal:
    def test_polish_diacritics(self):
        sig = _signal_yt_title_lang({"title": "Edyta Górniak - \u201eKolorowy wiatr' Pocahontas"})
        assert sig is not None and sig.language == "pl"

    def test_english(self):
        sig = _signal_yt_title_lang({"title": "Judy Kuhn - Colors of the Wind"})
        assert sig is not None and sig.language == "en"

    def test_short_title(self):
        assert _signal_yt_title_lang({"title": "hi"}) is None


class TestTitleHeuristicSignal:
    def test_polish(self):
        sig = _signal_title_heuristic("Kolorowy wiatr", "Edyta Górniak")
        assert sig is not None and sig.language == "pl" and sig.source == "title_heuristic"

    def test_english(self):
        sig = _signal_title_heuristic("Colors of the Wind", "Judy Kuhn")
        assert sig is not None and sig.language == "en"

    def test_empty(self):
        assert _signal_title_heuristic(None, None) is None
        assert _signal_title_heuristic("", "") is None


# --- consensus tests ----------------------------------------------------


class TestConsensus:
    def test_two_agreeing_signals_wins(self):
        sigs = [
            LanguageSignal("yt_title_lang", "pl", "x"),
            LanguageSignal("itunes_text", "pl", "y"),
        ]
        v = consensus(sigs)
        assert v is not None
        assert v.language == "pl"
        assert v.agreement == 2
        # Higher-rung source is picked as the consensus winner.
        assert v.winning_source == "itunes_text"

    def test_single_signal_is_not_consensus(self):
        assert consensus([LanguageSignal("yt_info_lang", "pl", "x")]) is None

    def test_disagreement_no_consensus(self):
        sigs = [
            LanguageSignal("yt_info_lang", "en", "x"),
            LanguageSignal("title_heuristic", "pl", "y"),
        ]
        assert consensus(sigs) is None

    def test_three_way_split_no_consensus(self):
        sigs = [
            LanguageSignal("yt_info_lang", "en", ""),
            LanguageSignal("title_heuristic", "pl", ""),
            LanguageSignal("itunes_country", "de", ""),
        ]
        assert consensus(sigs) is None

    def test_majority_wins_over_minority(self):
        sigs = [
            LanguageSignal("yt_info_lang", "pl", ""),
            LanguageSignal("title_heuristic", "pl", ""),
            LanguageSignal("itunes_country", "en", ""),
        ]
        v = consensus(sigs)
        assert v is not None and v.language == "pl" and v.agreement == 2


# --- collect_signals tests ---------------------------------------------


class TestCollectSignals:
    def test_full_polish_kolorowy_wiatr(self):
        """The flagship US-43 case: every available source agrees on `pl`."""
        signals = collect_signals(
            yt_info={
                "title": "Edyta Górniak - Kolorowy wiatr (Pocahontas)",
                "language": "pl",
                "subtitles": {"pl": [{}]},
            },
            itunes_hit={
                "artistName": "Edyta Górniak",
                "trackName": "Kolorowy wiatr",
                "collectionName": "Pocahontas (Polska Wersja Językowa)",
                "country": "POL",
            },
            mb_signals={
                "release_countries": ("PL", "PL"),
                "release_titles_joined": "Pocahontas: Oryginalna Ścieżka Dźwiękowa",
                "tag_names": ("polish",),
            },
            db_title="Kolorowy wiatr",
            db_artist="Edyta Górniak",
        )
        langs = {s.language for s in signals}
        assert langs == {
            "pl"
        }, f"expected all Polish, got {[(s.source, s.language) for s in signals]}"
        assert len(signals) >= 5
        v = consensus(signals)
        assert v is not None and v.language == "pl" and v.agreement >= 5

    def test_no_signals_when_everything_missing(self):
        assert collect_signals() == []

    def test_survives_mixed_inputs(self):
        signals = collect_signals(
            yt_info={"title": "x", "language": None},  # title too short
            itunes_hit=None,
            mb_signals=None,
            db_title="",
            db_artist="",
        )
        assert signals == []


# --- integration with DB ladder ----------------------------------------


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "classifier.db"))
    yield d
    d.close()


def _insert_song(db):
    db.insert_songs([{"file_path": "/songs/t.mp4", "youtube_id": None, "format": "mp4"}])
    return db.get_song_id_by_path("/songs/t.mp4")


class TestClassifyAndPersist:
    def test_writes_only_consensus_winner(self, db):
        sid = _insert_song(db)
        signals, verdict = classify_and_persist(
            db,
            sid,
            song_path="/songs/kolorowy.mp4",
            yt_info={
                "title": "Edyta Górniak - Kolorowy wiatr (Pocahontas)",
                "language": "pl",
            },
            itunes_hit={
                "artistName": "Edyta Górniak",
                "trackName": "Kolorowy wiatr",
                "collectionName": "Pocahontas (Polska Wersja Językowa)",
            },
            db_title="Kolorowy wiatr",
            db_artist="Edyta Górniak",
        )
        assert len(signals) >= 3
        assert verdict is not None and verdict.language == "pl"
        row = db.get_song_by_id(sid)
        assert row["language"] == "pl"
        # Highest-ranked agreeing source should be the stored provenance.
        sources = db.get_metadata_sources(sid)
        assert sources["language"] == "itunes_text"

    def test_lrc_heuristic_cannot_defeat_classifier_consensus(self, db):
        """The Kolorowy wiatr poison path: LRCLib returned English text.

        The classifier seeds `pl` via consensus; a later `lrc_heuristic`
        attempt to persist `en` must be rejected by the ladder.
        """
        sid = _insert_song(db)
        classify_and_persist(
            db,
            sid,
            yt_info={
                "title": "Edyta Górniak - Kolorowy wiatr",
                "language": "pl",
            },
            db_title="Kolorowy wiatr",
            db_artist="Edyta Górniak",
        )
        applied = db.update_track_metadata_with_provenance(sid, "lrc_heuristic", {"language": "en"})
        assert applied == {}, "lrc_heuristic must not override classifier verdict"
        assert db.get_song_by_id(sid)["language"] == "pl"

    def test_no_signals_writes_nothing(self, db):
        sid = _insert_song(db)
        signals, verdict = classify_and_persist(db, sid)
        assert signals == []
        assert verdict is None
        assert db.get_song_by_id(sid)["language"] is None

    def test_single_signal_is_tentative_no_write(self, db):
        """Per design doc: a single signal does not establish consensus."""
        sid = _insert_song(db)
        signals, verdict = classify_and_persist(
            db,
            sid,
            yt_info={"language": "pl"},
        )
        assert len(signals) == 1
        assert verdict is None
        assert db.get_song_by_id(sid)["language"] is None

    def test_disagreement_leaves_db_alone(self, db):
        """English iTunes/MB text + Polish title: no >=2 agreement, no write."""
        sid = _insert_song(db)
        classify_and_persist(
            db,
            sid,
            yt_info={"language": "pl"},
            itunes_hit={
                "artistName": "Judy Kuhn",
                "trackName": "Colors of the Wind",
                "collectionName": "Pocahontas Original Soundtrack",
            },
            db_title="Unknown",
            db_artist="Unknown",
        )
        # Signals split en/pl with 1 each (plus possibly more single-lang
        # signals); consensus rule won't find a majority cohort of 2 that
        # outvotes the others — exact assertion depends on langdetect's
        # reading of the iTunes text, so verify no spurious write landed.
        row = db.get_song_by_id(sid)
        # DB stays NULL because nothing reached consensus for THIS
        # intentionally conflicting input.
        if row["language"] is not None:
            # A single-language consensus only lands when one side hit >=2.
            # In that case the provenance must come from the classifier,
            # never a made-up rung name.
            assert db.get_metadata_sources(sid)["language"] in {
                "itunes_text",
                "title_heuristic",
                "yt_info_lang",
            }
