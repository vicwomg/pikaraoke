#!/bin/bash

echo "Usage: TAG=[tag] $0 [additional docker build arguments]"

#function GetPrefix (src)
#The function follows symbolic links until it gets the address of a directory or file.
#Allows you to create symbolic links in /usr/bin without breaking the prefix to program files.
#parameter src - link or path  to a file or directory
#returns the absolute path  to the directory where this file or directory is located.

function GetPrefix {
    SRC=$1
    while [[ -L "${SRC}" ]]
    do
    SRC=$(readlink ${SRC})
    done
    echo $(dirname $(realpath $SRC))
}

#set prefix to parent dir
PREFIX=$(dirname $(GetPrefix $0))

#CONST
DEFAULT_TAG="rhome/pikaraoke"
#DOCKERFILE=Dockerfile
DOCKERFILE=Dockerfile.alpine
#PLATFORM=linux/arm64,linux/amd64
PLATFORM=linux/amd64
ARGS="--network=host"

# The first argument is the tag
TAG=${TAG:-${DEFAULT_TAG}}
# Shift the arguments so that $2 becomes $1, $3 becomes $2, etc.
shift

(cd ${PREFIX} && docker buildx build ${ARGS}  -f ${DOCKERFILE} --platform ${PLATFORM} . -t $TAG "$@")
