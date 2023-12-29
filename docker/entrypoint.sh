#!/bin/sh

figlet PiKaraoke

# Read Docker host's hostname if URL is not set
if [ -z "$URL" ]; then
    if [ -f /etc/docker_hostname ]; then
        URL=$(cat /etc/docker_hostname):5555
        echo "URL is set to Docker Hostname: $URL, open your players browser to http://$URL:5555/splash"
    else
        echo "URL was not set and Docker hostname could not be found. Users and guests will need to manually connect to http://(docker hostname):5555. The displayed QR code will not function properly."
        URL_VARIABLE=""
    fi
else
    URL_VARIABLE="-u $URL"
fi

# Run pikaraoke with necessary parameters
if [ -z "$PASSWORD" ]; then
    /pikaraoke/pikaraoke.sh -d /pikaraoke-songs/ --headless $URL_VARIABLE
else
    /pikaraoke/pikaraoke.sh -d /pikaraoke-songs/ --headless $URL_VARIABLE --admin-password $PASSWORD
fi

# Keep the container running
tail -f /dev/null
