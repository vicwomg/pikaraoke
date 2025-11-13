#!/bin/bash

export TAG=rhome/pikaraoke-debian 
export DOCKERFILE=docker/Dockerfile.debian
export PLATFORM=linux/amd64

#comment next line for use default image from Dockerfile
#IMAGE=python:3.12-slim-bullseye 
[[ -n "$IMAGE" ]] && IMAGE_ARG="--build-arg IMAGE=${IMAGE}"

./build.sh  ${IMAGE_ARG} "$@"
