FROM python:3.12-slim

# Install required packages
RUN apt-get update --allow-releaseinfo-change && \
    apt-get install -y --no-install-recommends ffmpeg wireless-tools curl unzip && \
    apt-get clean && \
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y && \
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
