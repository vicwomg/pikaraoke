# Use bullseye over bookworm for better image size and ffmpeg compatibility
FROM python:3.12-slim-bullseye

# Install required packages
RUN apt-get update --allow-releaseinfo-change
RUN apt-get install ffmpeg wireless-tools -y

WORKDIR /app

# Copy minimum required files into the image
COPY pyproject.toml ./
COPY pikaraoke ./pikaraoke
COPY docs ./docs

# Install pikaraoke
RUN pip install .

COPY docker/entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
