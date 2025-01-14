# Use bullseye over bookworm for better image size and ffmpeg compatibility
FROM python:3.12-slim-bullseye

# Install required packages
RUN apt-get update --allow-releaseinfo-change && \
    apt-get install -y --no-install-recommends ffmpeg wireless-tools && \
    apt-get clean && \
    pip install poetry && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy minimum required files into the image
COPY pyproject.toml ./
COPY docs ./docs

# Only install main dependencies for better docker caching
RUN poetry install --only main --no-root

# Copy the rest of the files and install the remaining deps in a separate layer
COPY pikaraoke ./pikaraoke
RUN poetry install

ENTRYPOINT ["poetry", "run", "pikaraoke", "-d", "/app/pikaraoke-songs/", "--headless"]
