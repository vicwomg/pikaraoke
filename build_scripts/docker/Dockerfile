FROM alpine:edge

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.26 /uv /uv/bin/
ENV PATH="/uv/bin:$PATH"
ENV UV_BREAK_SYSTEM_PACKAGES=1

# Copy static ffmpeg binaries 5.1.1 is a few revisions old, but small and supports librubberband
COPY --from=mwader/static-ffmpeg:5.1.1 /ffprobe /ffmpeg /usr/local/bin/

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
RUN mkdir -p /app/pikaraoke-songs
# config directory must exist prior to run, otherwise the local volume mapping wont work
RUN mkdir -p /root/.pikaraoke
VOLUME ["/app/pikaraoke-songs", "/root/.pikaraoke"]
EXPOSE 5555

HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5555/ || exit 1

ENTRYPOINT ["pikaraoke", "-d", "/app/pikaraoke-songs/", "--headless"]