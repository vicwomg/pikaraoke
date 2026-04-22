"""YouTube Data API v3 — caption availability lookup.

Used to pre-flag search results with caption info so the search page can
skip the slow per-video yt-dlp probe (~1-3s each) for videos the API
already classified. Requires a Data API key in the ``youtube_data_api_key``
preference; when empty, callers fall back to the yt-dlp probe path.

Quota cost: ``videos.list`` with ``part=contentDetails`` is 1 unit per
call regardless of batch size (up to 50 IDs). A 10-result search page
therefore costs exactly 1 unit; the default free quota (10,000/day)
easily covers thousands of searches.

Only manual captions are reflected in the ``caption`` field returned by
the API — YouTube's auto-generated captions are not visible through Data
API v3. Callers treat ``manual=True`` as "definitely captioned" and fall
back to the yt-dlp probe to detect auto-captions.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_VIDEOS_LIST_URL = "https://www.googleapis.com/youtube/v3/videos"
_API_TIMEOUT_S = 5.0
_MAX_BATCH = 50  # API limit: 50 video IDs per videos.list call


def batch_caption_info(video_ids: list[str], api_key: str) -> dict[str, bool]:
    """Return ``{video_id: has_manual_captions}`` for every input ID.

    Missing IDs (deleted/private videos) map to ``False``. On any API error
    the call returns an empty dict so callers fall through to yt-dlp
    probes — a partial degradation is better than blocking the search
    page on a misconfigured key.
    """
    if not api_key or not video_ids:
        return {}
    # De-dupe while preserving order so we don't waste quota on repeats.
    seen: set[str] = set()
    ordered: list[str] = []
    for vid in video_ids:
        if vid and vid not in seen:
            seen.add(vid)
            ordered.append(vid)

    result: dict[str, bool] = {}
    for i in range(0, len(ordered), _MAX_BATCH):
        batch = ordered[i : i + _MAX_BATCH]
        try:
            result.update(_fetch_batch(batch, api_key))
        except requests.RequestException:
            logger.exception("YouTube Data API batch failed for %d ids", len(batch))
            return {}
        except ValueError:
            logger.exception("YouTube Data API returned non-JSON for %d ids", len(batch))
            return {}
    return result


def _fetch_batch(ids: list[str], api_key: str) -> dict[str, bool]:
    """Hit videos.list for a single batch (≤50 IDs). Raises on HTTP errors.

    Response shape (abridged): ``{"items": [{"id": "...", "contentDetails":
    {"caption": "true"/"false"}}, ...]}``. The ``caption`` field is a
    string, not a bool.
    """
    response = requests.get(
        _VIDEOS_LIST_URL,
        params={
            "part": "contentDetails",
            "id": ",".join(ids),
            "key": api_key,
        },
        timeout=_API_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()
    out: dict[str, bool] = {vid: False for vid in ids}
    for item in payload.get("items", []):
        vid = item.get("id")
        caption_str = item.get("contentDetails", {}).get("caption", "false")
        if vid:
            out[vid] = caption_str == "true"
    return out
