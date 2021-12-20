#!/bin/sh

read -p "Are you sure you want to setup PiKaraoke? (y/n): " REPLY
if [ $REPLY = "y" ]
 then

## setup stuff

echo
echo "*** RUNNING APT-GET UPDATE ***"
sudo apt-get update --allow-releaseinfo-change
if [ $? -ne 0 ]; then echo "ERROR: 'apt-get update' failed with error code: $?"; exit 1; fi

echo
echo "*** INSTALLING REQUIRED BINARIES ***"
sudo apt-get install libjpeg-dev libsdl2-image-2.0-0 vlc python3-pip ffmpeg libsdl2-ttf-dev -y
if [ $? -ne 0 ]; then echo "ERROR: Binary dependency installation failed with error code: $?"; exit 1; fi
# libsdl packages are required to make later versions of pygame work with fonts and images

echo
echo "*** PATCHING VLC TO RUN SUDO ***"
sudo sed -i 's/geteuid/getppid/' /usr/bin/vlc
if [ $? -ne 0 ]; then echo "ERROR: VLC patching failed with error code: $?"; exit 1; fi

echo
echo "*** INSTALLING LATEST YOUTUBE_DL ***"
sudo pip3 install --upgrade youtube_dl
if [ $? -ne 0 ]; then echo "ERROR: YouTube_dl installation failed with error code: $?"; exit 1; fi

echo
echo "*** INSTALLING PYTHON DEPENDENCIES ***"
sudo pip3 install -r requirements.txt
if [ $? -ne 0 ]; then echo "ERROR: Python requirements.txt installation failed with error code: $?"; exit 1; fi

BOOT_CONFIG=/boot/config.txt
BOOT_CONF_BACKUP=/boot/config.$(date +%s).old

echo
echo "*** ADDING PIKARAOKE CONFIGS ***"
more $BOOT_CONFIG | grep ^\#START_PIKARAOKE_CHANGES 
if [ $? -ne 1 ]; then 
  echo "WARN: There are old pikaraoke settings in your ${BOOT_CONFIG}! I don't want to mess with it."; 
else
  echo
  echo "*** BACKING UP OLD CONFIG.TXT ***"
  cp $BOOT_CONFIG $BOOT_CONF_BACKUP
  echo "Appending pikaraoke changes to $BOOT_CONFIG"
  sudo sh -c "echo >> $BOOT_CONFIG"
  sudo sh -c "more ./scripts/config.txt >> $BOOT_CONFIG"
  CONFIG_MODIFIED=1
fi

echo
echo "*** DONE! (yay, no errors) ***"
if [ $CONFIG_MODIFIED -eq 1 ]; then 
  echo "Your /boot/config.txt setting was modified, so you need to reboot:  sudo reboot"
fi
echo "Run PiKaraoke with:  sudo python3 app.py"
echo

# end setup stuff

else 
echo "bye."

fi
