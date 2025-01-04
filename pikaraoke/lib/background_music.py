import os
import random
import urllib


def create_randomized_playlist(input_directory, base_url, max_songs=50):
    # Get all mp3 files in the given directory
    files = [
        f
        for f in os.listdir(input_directory)
        if f.lower().endswith(".mp3") or f.lower().endswith(".mp4")
    ]

    # Shuffle the list of mp3 files
    random.shuffle(files)
    files = files[:max_songs]

    # Create the playlist
    playlist = []
    for mp3 in files:
        mp3 = urllib.parse.quote(mp3.encode("utf8"))
        url = f"{base_url}/{mp3}"
        playlist.append(f"{url}")

    return playlist
