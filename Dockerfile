FROM python:3.12-slim-bookworm

# Install required packages
RUN apt-get update --allow-releaseinfo-change
RUN apt-get install ffmpeg -y
RUN apt-get install chromium -y
RUN apt-get install chromium-driver -y

# Copy contents of the project into the image
RUN mkdir pikaraoke
COPY pikaraoke pikaraoke/pikaraoke
COPY scripts/requirements.txt pikaraoke/
COPY scripts/entrypoint.sh pikaraoke/

# Install Python Dependencies
RUN pip3 install -r pikaraoke/requirements.txt

# Make entrypoint script executable
RUN chmod +x pikaraoke/entrypoint.sh

# Set Entrypoint
ENTRYPOINT ["pikaraoke/entrypoint.sh"]