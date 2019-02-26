#!/bin/sh
export PK_PORT=5000
export PK_THREADS=50
export PK_DOWNLOAD_PATH='/home/pi/pikaraoke/songs'

/usr/local/bin/gunicorn app:app -b 0.0.0.0:${PK_PORT} --threads ${PK_THREADS}
