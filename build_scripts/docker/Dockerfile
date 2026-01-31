FROM alpine:edge

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.26 /uv /uv/bin/
ENV PATH="/uv/bin:$PATH"
ENV UV_BREAK_SYSTEM_PACKAGES=1

# Copy static ffmpeg binaries. 7.0 is a bit old, but significantly smaller while supporting librubberband
COPY --from=mwader/static-ffmpeg:7.0 /ffprobe /ffmpeg /usr/local/bin/

RUN --mount=type=cache,target=/var/cache/apk \
    apk add --update-cache python3 deno

# Copy dependency and metadata files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies using uv (this layer is cached)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache-dir -r pyproject.toml

COPY docs ./docs
COPY pikaraoke ./pikaraoke

# Final install of the package itself (fast)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache-dir --no-deps .

# Set up volumes and ports

# Create a non-root user to run as
RUN adduser -D -u 1000 pikaraoke

# Directories must exist prior to run, otherwise the local volume persistence won't work
RUN mkdir -p /home/pikaraoke/.pikaraoke && \
    mkdir -p /app/pikaraoke-songs && \
    chown -R pikaraoke:pikaraoke /app/pikaraoke-songs /home/pikaraoke

# Set environment variables for the user
ENV HOME=/home/pikaraoke
USER pikaraoke

VOLUME ["/app/pikaraoke-songs", "/home/pikaraoke/.pikaraoke"]
EXPOSE 5555

HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5555/ || exit 1

ENTRYPOINT ["pikaraoke", "-d", "/app/pikaraoke-songs/", "--headless"]