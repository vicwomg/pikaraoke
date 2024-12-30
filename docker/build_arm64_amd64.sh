#!/bin/bash

# Check if at least one argument is provided
if [ $# -lt 1 ]; then
  echo "Usage: $0 <tag> [additional docker build arguments]"
  echo "Can only be run from the project root directory"
  exit 1
fi

# The first argument is the tag
TAG=$1

# Shift the arguments so that $2 becomes $1, $3 becomes $2, etc.
shift

docker buildx build --platform linux/arm64,linux/amd64 . -t $TAG "$@"
