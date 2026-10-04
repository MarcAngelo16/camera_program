# Build context is expected to be the repo root (camera_program/), so this
# Dockerfile can reach both webapp/ and the vendor lib/include directories.
FROM python:3.11-slim

# Match this to the server's architecture: lib/x64 (x86_64) or lib/arm64 (aarch64).
ARG SDK_LIB_DIR=lib/x64

COPY ${SDK_LIB_DIR}/libMVSDK.so /usr/lib/libMVSDK.so
RUN ldconfig

# ffmpeg transcodes each recording chunk (MSCV -> H.264) after it closes --
# see app/transcode.py. Needed on any edge box that records, not on a
# camera-less instance like the central VPS, but installing it everywhere
# this image runs is simpler than maintaining two Dockerfiles.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY webapp/app ./app
COPY webapp/static ./static

ENV STORAGE_DIR=/app/storage
RUN mkdir -p /app/storage
VOLUME /app/storage

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
