#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate

if [[ -n "$SSH_CLIENT" || -n "$SSH_TTY" ]]; then
  if [[ "$DISPLAY" != ":0.0" ]]; then
    echo "Warning: Running remotely via SSH. Setting DISPLAY=:0.0 to run on host display"
    export DISPLAY=:0.0
  else
    echo "DISPLAY is correctly set for SSH session."
  fi
fi

python3 app.py $@
