#!/bin/sh

read -p "Are you sure you want to setup PiKaraoke? (y/n): " REPLY
if [ $REPLY = "y" ]
 then

## setup stuff

echo
echo "*** RUNNING APT-GET UPDATE ***"
sudo apt-get update
if [ $? -ne 0 ]; then echo "ERROR: 'apt-get update' failed with error code: $?"; exit 1; fi

echo
echo "*** INSTALLING REQUIRED BINARIES ***"
sudo apt-get install libjpeg-dev omxplayer vlc python3-pip python-pygame ffmpeg -y
if [ $? -ne 0 ]; then echo "ERROR: Binary dependency installation failed with error code: $?"; exit 1; fi

echo
echo "*** PATCHING VLC TO RUN SUDO ***"
sudo sed -i 's/geteuid/getppid/' /usr/bin/vlc
if [ $? -ne 0 ]; then echo "ERROR: VLC patching failed with error code: $?"; exit 1; fi

echo
echo "*** INSTALLING LATEST YOUTUBE_DL from l1ving repo ***"
# sudo pip3 install --upgrade youtube_dl
sudo pip3 uninstall youtube-dl -y
sudo pip3 install -U git+https://github.com/l1ving/youtube-dl
if [ $? -ne 0 ]; then echo "ERROR: YouTube_dl installation failed with error code: $?"; exit 1; fi

echo
echo "*** INSTALLING PYTHON DEPENDENCIES ***"
sudo pip3 install -r requirements.txt
if [ $? -ne 0 ]; then echo "ERROR: Python requirements.txt installation failed with error code: $?"; exit 1; fi

echo
echo "*** INSTALLING NodeJS and LOCAL YOUTUBE-PARSE repo ***"
sudo curl -sL https://deb.nodesource.com/setup_14.x | sudo -E bash -
sudo apt install -y nodejs
if [ $? -ne 0 ]; then echo "ERROR: NPM installation failed with error code: $?"; exit 1; fi
sudo git clone https://github.com/HermanFassett/youtube-scrape.git
sudo cd youtube-scrape
sudo npm install
sudo cd ..

echo
echo "*** BUMPING UP GPU MEMORY ***"
echo "Getting your current gpu mem..."
BOOT_CONFIG=/boot/config.txt
more $BOOT_CONFIG | grep ^gpu_mem=
if [ $? -ne 1 ]; then 
  echo "WARN: There's a gpu_mem setting in your ${BOOT_CONFIG}! I don't want to mess with it."; 
  echo "If the above line reads gpu_mem=128 or greater, you should be ok"
  echo "If it's less than 128, you may experience visual artifacts during video playback."
  GPU_SET=2
else
  echo "No current gpu_mem setting found."
  echo "Appending gpu_mem=128 to $BOOT_CONFIG"
  sudo sh -c "echo \"gpu_mem=128\" >> $BOOT_CONFIG"
  GPU_SET=1
fi

echo
echo "*** DONE! (yay, no errors) ***"
if [ $GPU_SET -eq 1 ]; then 
  echo "Your gpu_mem setting was modified, so you need to reboot:  sudo reboot"
fi
echo "Run PiKaraoke with:  sudo python3 app.py"
echo

# end setup stuff

else 
echo "bye."

fi

