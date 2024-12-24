#!/bin/sh

# Run pikaraoke with necessary parameters
pikaraoke -d /app/pikaraoke-songs/ --headless $EXTRA_ARGS

# Keep the container running
tail -f /dev/null
