"""Choosing which background video the splash screen plays."""

import os
import random

# The set every target browser plays. .mov is h264 in a QuickTime container
# often enough to be tempting and fails often enough outside Safari to become a
# support question; .mkv and .avi are not browser formats.
VIDEO_EXTENSIONS = (".mp4", ".m4v", ".webm")


def video_choices(path: str | None, limit: int = 50) -> list[str]:
    """The videos at `path` the splash screen picks one of each time it idles.

    Resolved as the page renders, never per request: the route answers range
    requests, so choosing per request would serve one video's bytes against
    another video's length. Shuffled before the limit is applied so a folder
    larger than it does not always offer the same videos.
    """
    if path is None or not os.path.exists(path):
        return []
    names = [os.path.basename(path)] if os.path.isfile(path) else os.listdir(path)
    videos = [name for name in names if name.lower().endswith(VIDEO_EXTENSIONS)]
    random.shuffle(videos)
    return videos[:limit]
