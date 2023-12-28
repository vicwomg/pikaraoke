#!/bin/sh

# Correcting the URL format
URL=$(echo "$URL" | sed 's|/$||')  # Remove trailing slash if exists
if ! echo "$URL" | grep -q "^https://"; then
  URL="https://$URL"  # Add https:// if not present
fi

# Exporting the corrected URL
export URL

# Run pikaraoke with environment variables
if [ -z "$PASSWORD" ]; then
  figlet PiKaraoke
  /pikaraoke/pikaraoke.sh -d /pikaraoke-songs/ --headless -u $URL
else
  figlet PiKaraoke
  /pikaraoke/pikaraoke.sh -d /pikaraoke-songs/ --headless -u $URL --admin-password $PASSWORD
fi

# Keep the container running
tail -f /dev/null
