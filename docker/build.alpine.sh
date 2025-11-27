#!/bin/bash

export TAG=rhome/pikaraoke-alpine
export DOCKERFILE=docker/Dockerfile.alpine
export PLATFORM=linux/amd64

#comment next line for use default image from Dockerfile
#IMAGE=alpine:edge
[[ -n "$IMAGE" ]] && IMAGE_ARG="--build-arg IMAGE=${IMAGE}"

./build.sh  ${IMAGE_ARG} "$@"
