FROM python:3.12-slim

# ffmpeg provides both ffmpeg and ffprobe binaries.
# libgles2 + libegl1 satisfy NiceGUI's headless rendering requirements on Linux.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgles2 libegl1 curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, reproducible dependency resolution.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app
COPY . .

# CPU-only torch wheels avoid the 8 GB CUDA runtime while keeping full
# decode and render functionality (torchcodec runs on CPU just fine). The
# BuildKit cache mount keeps uv's download cache (torch is ~700 MB) across
# builds, so editing source no longer re-downloads the wheels.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --index-strategy unsafe-best-match \
    ".[all]"

# NiceGUI must bind to 0.0.0.0 for the Docker port mapping to be reachable.
ENV ANNIE_HOST=0.0.0.0
ENV ANNIE_PORT=8080
ENV ANNIE_HOME=/annie-home
# The package is installed into site-packages, so it cannot locate the bundled
# example data by walking up from __file__; point ANNIE_DATA_DIR at the copy in
# the image so example configs still appear in the Dataset dropdown.
ENV ANNIE_DATA_DIR=/app/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:${ANNIE_PORT}/ || exit 1

ENTRYPOINT ["annie"]
