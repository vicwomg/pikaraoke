#!/bin/bash

source /home/pi/pikaraoke/.venv/bin/activate
python3 app.py -y /home/pi/pikaraoke/.venv/bin/yt-dlp -d ~/songs 
