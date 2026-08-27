"""Choosing which background video the splash screen plays."""

import os
import random

# The set every target browser plays. .mov is h264 in a QuickTime container
# often enough to be tempting and fails often enough outside Safari to become a
# support question; .mkv and .avi are not browser formats.
VIDEO_EXTENSIONS = (".mp4", ".m4v", ".webm")


def video_directory(path: str) -> str:
    """The folder videos are served from, whether the path names one or holds them."""
    return os.path.dirname(path) if os.path.isfile(path) else path


def pick_video(path: str | None) -> str | None:
    """The name of one video from `path`, picked at random when it is a directory.

    Resolved once as the splash screen renders, never per request: the route
    answers range requests, so choosing per request would serve one video's
    bytes against another video's length.
    """
    if path is None or not os.path.exists(path):
        return None
    if os.path.isfile(path):
        name = os.path.basename(path)
        return name if name.lower().endswith(VIDEO_EXTENSIONS) else None
    videos = [f for f in os.listdir(path) if f.lower().endswith(VIDEO_EXTENSIONS)]
    return random.choice(videos) if videos else None
