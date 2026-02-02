#!/bin/bash
# Script to run a pikaraoke Docker container and verify that it initializes correctly.

IMAGE_NAME=${1:-"pikaraoke-ci-test:latest"}
CONTAINER_NAME=${2:-"pikaraoke-test"}

echo "Running smoketest for image: $IMAGE_NAME (container: $CONTAINER_NAME)"

docker run -d --name "$CONTAINER_NAME" "$IMAGE_NAME"

# Wait for initialization (max 60s for emulation)
INITIALIZED=false
for i in {1..60}; do
  if docker logs "$CONTAINER_NAME" 2>&1 | grep -q "Connect the player host to:"; then
    echo "Found expected initialization output."
    INITIALIZED=true
    break
  fi
  sleep 1
done

if [ "$INITIALIZED" = false ]; then
  echo "Error: Timed out waiting for PiKaraoke to initialize."
  docker logs "$CONTAINER_NAME"
  exit 1
fi

docker rm -f "$CONTAINER_NAME"