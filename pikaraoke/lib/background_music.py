import os
import random
import urllib


def create_randomized_playlist(input_directory, base_url):
    # Get all mp3 files in the given directory
    mp3_files = [f for f in os.listdir(input_directory) if f.endswith(".mp3")]

    # Shuffle the list of mp3 files
    random.shuffle(mp3_files)

    # Create the playlist
    playlist = []
    for mp3 in mp3_files:
        mp3 = urllib.parse.quote(mp3.encode("utf8"))
        url = f"{base_url}/{mp3}"
        playlist.append(f"{url}")

    return playlist
