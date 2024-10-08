#!/bin/sh

figlet PiKaraoke

# Run pikaraoke with necessary parameters
cd pikaraoke
poetry run pikaraoke -d /pikaraoke-songs/ --headless --url $URL:5555 --ffmpeg-url $URL:5556

# Keep the container running
tail -f /dev/null