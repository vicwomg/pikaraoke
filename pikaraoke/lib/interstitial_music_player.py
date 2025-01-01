import os
import random


def create_randomized_playlist(
    input_directory, base_url, output_directory, playlist_name="playlist.m3u"
):
    # Get all mp3 files in the given directory
    mp3_files = [f for f in os.listdir(input_directory) if f.endswith(".mp3")]

    # Shuffle the list of mp3 files
    random.shuffle(mp3_files)

    # Create the playlist file
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)
    playlist_path = os.path.join(output_directory, playlist_name)
    with open(playlist_path, "w") as playlist_file:
        for mp3 in mp3_files:
            url = f"{base_url}/{mp3}"
            playlist_file.write(f"{url}\n")

    print(f"Playlist created: {playlist_path}")


# Example usage
# create_randomized_playlist('/path/to/your/mp3/directory', 'http://localhost:5555/music', '/path/to/output/directory')
