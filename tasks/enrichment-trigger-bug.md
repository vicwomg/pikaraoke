# Bug: iTunes/MusicBrainz enrichment never auto-runs on download

## Symptom

All recently downloaded songs (12/12 inspected, IDs 100–111, downloaded
2026-05-03) have:

- `metadata_status = 'pending'`
- `enrichment_attempts = 0`
- `last_enrichment_attempt = NULL`
- `artist = ''`, `title = ''`
- `metadata_sources = {"language": "whisper_probe_raw"}` (only Whisper
  has written anything; no `youtube` / `itunes` / `musicbrainz` entries)

## Why it matters

Without enrichment:

- LRCLib / Genius / Spotify / Tekstowo queries fall back to filename-
  derived artist/title, which is often truncated ("Krzysztof Kraw"
  instead of "Krzysztof Krawczyk", from a yt-dlp filename clipped to a
  filesystem-safe length).
- `tekstowo-sync` and `genius-sync` quietly miss songs that would match
  if the canonical iTunes name was in the DB.
- `spotify-sync` loses the deterministic ISRC search path and has to
  fall back to `track:"…" artist:"…"` matching.

## Manual verification

Manually invoking `enrich_song(db, song_id, file_path)` for these songs
works correctly:

- `id=105` ("Brodka") → `artist='Brodka'`, ISRC `PLA330500065` added,
  `metadata_status='enriched'`, `enrichment_attempts=1`
- `id=106` ("Krzysztof Kraw") → `artist='Krzysztof Krawczyk'` (iTunes
  did normalise the truncated name), `title='Za tobą pójdę jak na bal'`,
  album added

So the enricher itself is not broken. The trigger pipeline is.

## Suspected root cause

`song_manager.SongManager.register_download` (line 295) is wired to the
`song_downloaded` event in `karaoke.py:488` and unconditionally calls
`_start_enrichment` when `_enrich_on_download=True` (default). Yet
`enrichment_attempts=0` for every download means the pipeline never
reaches `stamp_enrichment_attempt` (which `enrich_song` calls on every
exit path, including failure).

Three possibilities:

1. **`register_download` is not invoked for downloads.** The
   `LibraryScanner` may be racing in via `insert_songs` /
   `_backfill_artifacts` before the `song_downloaded` event fires, and
   `register_download` either short-circuits on duplicate insert or the
   event handler silently drops. (Note: current `register_download` does
   NOT short-circuit — it always proceeds to `_start_enrichment`.)
2. **The daemon thread crashes early** in `enrich_song` (lines 119–122,
   `db.get_song_by_id` returning `None` triggers an unstamped early
   return). PiKaraoke has no persistent log file, so any thread-level
   exception is invisible after the fact.
3. **`_track_metadata_from_info_json` returns `{}`** (info.json missing
   or empty after the consume-and-delete step). That alone wouldn't
   block enrichment — `enrich_song` falls back to filename — but if
   combined with possibility 2 (early return on stale `song_id`), it
   could conspire.

## Reproduction steps to nail down root cause

1. Add `logger.info("register_download CALLED for %s", song_path)` and
   `logger.info("_start_enrichment dispatched for song_id=%d", song_id)`
   to `song_manager.py`.
2. Add `logger.info("enrich_song START %s", song_path)` and a `try/
   finally` that always logs at exit in `song_enricher.py:enrich_song`.
3. Download a song through the UI and grep the logs for those markers.
4. If `register_download CALLED` is missing → check event wiring
   (`karaoke.py:488`) and `LibraryScanner` interaction.
5. If `enrich_song START` is missing → daemon thread is dying before
   the deferred import.

## Suggested workaround until root cause is fixed

A one-shot backfill script:

```python
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.song_enricher import enrich_song

db = KaraokeDatabase()
with db._lock:
    rows = db._conn.execute(
        "SELECT id, file_path FROM songs "
        "WHERE metadata_status='pending' AND enrichment_attempts=0"
    ).fetchall()
for song_id, file_path in rows:
    enrich_song(db, song_id, file_path)
```

## Discovered while

Wiring up `tekstowo-sync` (PR `2850cd7a`). 6/8 Polish-song hit rate on
live tekstowo.pl, with 2 misses traced to truncated metadata. iTunes
fixes "Krzysztof Kraw" → "Krzysztof Krawczyk"; nothing fixes
"Brodka" → "Monika Brodka" (iTunes only knows her as "Brodka").
