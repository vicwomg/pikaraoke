#!/bin/sh

figlet PiKaraoke

# Run pikaraoke with necessary parameters
cd pikaraoke
poetry run pikaraoke -d /pikaraoke-songs/ --headless --high-quality

# Keep the container running
tail -f /dev/null