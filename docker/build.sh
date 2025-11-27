#!/bin/bash

echo "Usage: [TAG=tag] [PLATFORM=paltform] [DOCKERFILE=Dockerfile] [ARGS=args] [EXTRA=extra]  $0 [additional docker build arguments]"
echo "Example: TAG=pikaraoke-test PLATFORM=linux/x86 DOCKERFILE=Dockerfile.bookworm  $0 [additional docker build arguments]"
cat << EOF 
Usage: [TAG=tag] [PLATFORM=paltform] [DOCKERFILE=Dockerfile] [ARGS=args]   $0 [additional docker build arguments]
    TAG: docker image tag (for example: vendor/pikaraoke)
    PLATFORM: build image platform (for example: linux/arm64,linux/amd64)
    ARGS: default args 
Example: TAG=pikaraoke-test PLATFORM=linux/x86 DOCKERFILE=Dockerfile.bookworm  $0 [additional docker build arguments]

EOF


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

#DEFAULT

DEFAULT_TAG=rhome/pikaraoke-edge
DEFAULT_DOCKERFILE=docker/Dockerfile.alpine
DEFAULT_PLATFORM=linux/amd64
#enable network by default
DEFAULT_ARGS="--network=host"

echo use build image parameters
for PARAM in TAG PLATFORM DOCKERFILE ARGS
do
    [[ -z "${!PARAM}" ]] && tmp="DEFAULT_${PARAM}" &&  eval ${PARAM}='${!tmp}'
    echo  "${PARAM} = ${!PARAM}"
done

echo "additional docker build arguments: $*"

CMD="docker buildx build -f ${DOCKERFILE}  -t ${TAG} ${ARGS} --platform ${PLATFORM} . $*" 
echo "build docker image command: ${CMD}"

#call docker build from parent directory and return 
(cd ${PREFIX} && ${CMD} )
