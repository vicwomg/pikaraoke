FROM python:3.12.8-slim

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update --allow-releaseinfo-change && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ffmpeg wireless-tools curl unzip && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- v2.1.4

COPY pyproject.toml ./
COPY docs ./docs

RUN pip install --no-cache-dir --prefer-binary --no-compile .

COPY pikaraoke ./pikaraoke

RUN pip install --no-cache-dir --no-deps -e . && \
    useradd -m -u 1000 pikaraoke && \
    mkdir -p /app/pikaraoke-songs && \
    chown -R pikaraoke:pikaraoke /app

EXPOSE 5555
VOLUME ["/app/pikaraoke-songs"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5555/ || exit 1

USER pikaraoke

ENTRYPOINT ["pikaraoke", "-d", "/app/pikaraoke-songs/", "--headless"]
