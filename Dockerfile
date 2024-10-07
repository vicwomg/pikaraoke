FROM python:3.12-slim-bookworm

# Install required packages
RUN apt-get update --allow-releaseinfo-change
RUN apt-get install figlet
RUN apt-get install ffmpeg -y
RUN apt-get install chromium -y
RUN apt-get install chromium-driver -y

# Add sbin to path as "iwconfig" lives there now
ENV PATH="$PATH:/sbin"

# Copy contents of the project into the image
RUN mkdir pikaraoke
COPY pikaraoke pikaraoke/pikaraoke
COPY pyproject.toml pikaraoke
COPY scripts/entrypoint.sh pikaraoke/

# Install Poetry
RUN pip install poetry

# Install dependencies
RUN cd pikaraoke && poetry install

# Make entrypoint script executable
RUN chmod +x pikaraoke/entrypoint.sh

# Set Entrypoint
ENTRYPOINT ["pikaraoke/entrypoint.sh"]