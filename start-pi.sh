#!/bin/bash

source .venv/bin/activate
python3 app.py -y .venv/bin/yt-dlp --hide-splash -l10
