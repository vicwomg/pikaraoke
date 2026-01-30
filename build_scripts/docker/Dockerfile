FROM python:3.12.8-slim

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.26 /uv /uv/bin/
ENV PATH="/uv/bin:$PATH"
ENV UV_BREAK_SYSTEM_PACKAGES=1

# Copy static ffmpeg binaries
COPY --from=mwader/static-ffmpeg:latest /ffprobe /ffmpeg /usr/local/bin/

# Install system dependencies with cache mounts
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update --allow-releaseinfo-change && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        wireless-tools curl unzip
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- v2.1.4

# Copy dependency and metadata files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies using uv (this layer is cached)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache-dir --no-compile -r pyproject.toml

COPY docs ./docs
COPY pikaraoke ./pikaraoke

# Re-install with -e and setup environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache-dir --no-deps -e . && \
    useradd -m -u 1000 pikaraoke && \
    mkdir -p /app/pikaraoke-songs && \
    chown -R pikaraoke:pikaraoke /app

EXPOSE 5555
VOLUME ["/app/pikaraoke-songs"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5555/ || exit 1

USER pikaraoke

ENTRYPOINT ["pikaraoke", "-d", "/app/pikaraoke-songs/", "--headless"]