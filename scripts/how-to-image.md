## Preliminary steps

- Get the image in good working order on the smallest SD card possible (Raspbian Lite on a 4GB card)
- On the source pi, boot it up the script in this directory and pay attention that the output matches the expected text 
- Remove SD card and plug into computer

## Creating a pi image in OSX

- Determine the path of SD card `diskutil list`
- Run dd, replacing the if param with the device path of the SD card `sudo dd bs=4m if=/dev/disk5 of=pikaraoke.img`
- Zip the image file, it should clock in under 1.5GB if all goes well
