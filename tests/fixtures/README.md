# Lyrics-alignment fixtures

Each subdirectory pins one song's vocal-onset list (silero VAD + ffmpeg
silencedetect, merged) and LRClib synced lyrics so the integration
tests in ``tests/integration/test_lyrics_align_*.py`` can replay
real-world data without network or media files in the repo.

## Layout

```
total_eclipse/
  vocal_onsets.json    # {audio_duration_s, onsets: [{onset, next_onset}]}
  lyrics.lrc           # LRClib syncedLyrics, one entry per line
mam_te_moc/
  vocal_onsets.json
  lyrics.lrc
queen_iwtbf/
  vocal_onsets.json
  lyrics.lrc
```

## Refresh

Capturing requires the ``[align]`` extra (silero) and ffmpeg, plus
the song's audio file (or its Demucs vocals stem - the pipeline runs
on whichever path you pass). The integration tests do **not** need
silero or ffmpeg at run time; they read the committed JSON + LRC.

```bash
# Best results: capture from the Demucs vocals stem (matches what
# WhisperXAligner.align actually feeds vad_probe in production).
python scripts/capture_alignment_fixture.py \
    --audio "$HOME/.pikaraoke-cache/<key>/vocals.mp3" \
    --artist "Bonnie Tyler" \
    --track "Total Eclipse of the Heart" \
    --slug total_eclipse

# The raw m4a/mp4 also works but silero's classifier is less reliable
# on dense rock mixes; per-phrase coverage is sparser.
```

The capture script writes both files atomically into the slug
directory. Commit the diff.

## When to refresh

* DP cost weights change (any of ``_DP_W_*`` in
  ``pikaraoke/lib/lyrics_align.py``).
* Silero / silencedetect parameter retune (any of ``_VAD_*`` /
  ``_FALLBACK_*`` in ``pikaraoke/lib/vad_probe.py``).
* LRClib's curated entry for one of these songs is hand-edited
  upstream (the test pins behaviour against the captured snapshot,
  not the live API - so refresh is *optional* in this case).
