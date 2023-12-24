#!/bin/bash

read -p "Are you sure you want to setup PiKaraoke? (y/n): " REPLY
if [ $REPLY = "y" ]
 then

## setup stuff

if [[ $(cat /etc/os-release | grep -i debian) != "" ]]; then
  echo "Client is a Debian-based system. Installing binaries"; 
  echo
  echo "*** INSTALLING REQUIRED BINARIES ***"
  sudo apt install --only-upgrade ffmpeg chromium-browser chromium-chromedriver -y
  if [ $? -ne 0 ]; then echo "ERROR: Binary dependency installation failed with error code: $?"; exit 1; fi
else  
 echo "Client is not Debian-based. Skipping binary installation. Please install ffmpeg and chrome manually."; 
fi

echo
echo "*** CREATING PYTHON VIRTUAL ENVIRONMENT ***"
python3 -m venv .venv
source .venv/bin/activate

echo
echo "*** INSTALLING PYTHON DEPENDENCIES ***"
pip3 install -r requirements.txt
if [ $? -ne 0 ]; then echo "ERROR: Python requirements.txt installation failed with error code: $?"; exit 1; fi

echo
echo "*** DONE ***"
echo "Run PiKaraoke with: ./pikaraoke.sh <args>"
echo

# end setup stuff

else 
echo "bye."

fi
