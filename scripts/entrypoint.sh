#!/bin/sh

figlet PiKaraoke

# Run pikaraoke with necessary parameters
cd pikaraoke
poetry run pikaraoke -d /pikaraoke-songs/ --headless $EXTRA_ARGS

# Keep the container running
tail -f /dev/null