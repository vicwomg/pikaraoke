#!/bin/bash
# Script to run a pikaraoke Docker container and verify that it initializes correctly.

docker run -d --name pikaraoke-test pikaraoke-ci-test:latest

# Wait for initialization (max 30s)
INITIALIZED=false
for i in {1..30}; do
  if docker logs pikaraoke-test 2>&1 | grep -q "Connect the player host to:"; then
    echo "Found expected initialization output."
    INITIALIZED=true
    break
  fi
  sleep 1
done

if [ "$INITIALIZED" = false ]; then
  echo "Error: Timed out waiting for PiKaraoke to initialize."
  docker logs pikaraoke-test
  exit 1
fi

docker rm -f pikaraoke-test