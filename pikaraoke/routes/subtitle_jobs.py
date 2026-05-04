"""Bulk lookup of per-source subtitle job state for the queue rosette.

Phase 2 of the subtitle UI: the queue list (and any future surface that needs
to render N songs at once) batches a single ``POST /api/songs/subtitles/bulk``
instead of N round-trips. The single-song picker on the now-playing-bar reads
its data from the already-pushed ``now_playing.subtitle_sources`` payload, so
no GET endpoint is exposed here — bulk only.

Source labels and DB→UI status translation live in ``karaoke_database.py`` so
this blueprint and the orchestrator agree on the exact wire format.
"""

from flask import jsonify, request
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin
from pikaraoke.lib.karaoke_database import (
    JOB_STATE_TO_UI_STATUS,
    SUBTITLE_SOURCE_LABELS,
)
from pikaraoke.lib.subtitle_orchestrator import DEFAULT_AUTO_SOURCES

subtitle_jobs_bp = Blueprint("subtitle_jobs", __name__)

# Hard ceiling on a single bulk request. The queue rendering N songs at once
# is the only intended caller; 100 is well above any realistic queue length
# and well below the JSON-response budget on Pi 3.
MAX_BULK = 100


def _serialize_job_row(row) -> dict:
    """Translate a ``subtitle_jobs`` DB row to the picker's UI DTO."""
    state = row["state"]
    return {
        "source": row["source"],
        "label": SUBTITLE_SOURCE_LABELS.get(row["source"], row["source"]),
        "state": state,
        "status": JOB_STATE_TO_UI_STATUS.get(state, state),
        "tier": row["tier"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "attempt_count": row["attempt_count"],
        "next_retry_at": row["next_retry_at"],
    }


def _placeholder_sources() -> list[dict]:
    """Synthetic ``na`` rows for songs the orchestrator has never run on.

    Pre-Phase-1 library entries have an empty ``subtitle_jobs`` table for
    their ``song_id``; rendering those as blanks would surprise the operator.
    Synthesize one row per canonical source so the picker shows the full
    column.
    """
    return [
        {
            "source": source,
            "label": SUBTITLE_SOURCE_LABELS.get(source, source),
            "state": None,
            "status": "na",
            "tier": None,
            "error_code": None,
            "error_message": None,
            "started_at": None,
            "finished_at": None,
            "attempt_count": 0,
            "next_retry_at": None,
        }
        for source in DEFAULT_AUTO_SOURCES
    ]


@subtitle_jobs_bp.route("/api/songs/subtitles/bulk", methods=["POST"])
def post_bulk():
    """Return per-source job state for many songs in one round-trip.

    Request body::

        {"song_ids": [int, ...]}

    Response body::

        {"<song_id>": {"sources": [...]}, ...}

    Songs that have no rows yet receive synthesized ``na`` placeholders so
    every key in the response is renderable. Duplicate ids in the request
    are silently de-duplicated.
    """
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403

    body = request.get_json(silent=True) or {}
    song_ids = body.get("song_ids")
    if not isinstance(song_ids, list):
        return jsonify({"error": "song_ids must be a list of integers"}), 400
    if not all(isinstance(sid, int) for sid in song_ids):
        return jsonify({"error": "song_ids must be a list of integers"}), 400
    if len(song_ids) > MAX_BULK:
        return jsonify({"error": f"too many ids (max {MAX_BULK})"}), 400
    if not song_ids:
        return jsonify({})

    k = get_karaoke_instance()
    if k.db is None:
        return jsonify({"error": "Database unavailable"}), 500

    bulk_rows = k.db.get_subtitle_jobs_bulk(song_ids)
    out: dict[str, dict] = {}
    placeholder = _placeholder_sources()
    for sid in set(song_ids):
        rows = bulk_rows.get(sid)
        if rows:
            out[str(sid)] = {"sources": [_serialize_job_row(r) for r in rows]}
        else:
            out[str(sid)] = {"sources": list(placeholder)}
    return jsonify(out)
