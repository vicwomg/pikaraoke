#!/bin/sh

read -p "This will clean a pikaraoke install to make it ready for image deployment. Proceed? (y/n): " REPLY
if [ $REPLY = "y" ]
 then

echo
echo "*** Ensure we're on the latest master..."
cd ~/pikaraoke
git checkout master
git pull

echo "*** Clearing the songs dir ***"
sudo rm -rf /usr/lib/pikaraoke

echo "*** Removing WPA supplicant saved wifi settings ***"
sudo rm -rf /etc/wpa_supplicant/wpa_supplicant.conf

echo "*** Double-checking /usr/share/alsa/alsa.conf"
cat /usr/share/alsa/alsa.conf | grep "defaults.ctl.card "
cat /usr/share/alsa/alsa.conf | grep "defaults.pcm.card "
echo "^ the above values should be set to 0, not 1"

echo "*** Double-checking /etc/rc.local expecting to see a line about launching pikaraoke:"
cat /etc/rc.local | grep pikaraoke

echo "*** Double-checking we have a wpa_supplicant example file in /boot"
ls -la /boot/wpa_supplicant.conf.example

echo "*** Expecting hostname to be 'pikaraoke'"
hostname

fi