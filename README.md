# Padel Camera Recording System

Self-serve match recording for padel fields: each field has a camera and a QR
code; a player scans it, starts/stops a recording of their match, and the
video is collected for later use (analytics/AI pipeline — not built yet).

This repo contains the vendor MindVision MVSDK (camera driver + headers) and,
under `webapp/`, the web application that controls a camera and records video.

See [`PLAN.md`](PLAN.md) for the full architecture, phases, and open
decisions. This file is just quick orientation + how to run things.

## What's here

```
include/            Vendor C API headers (CameraApi.h, CameraDefine.h, CameraStatus.h)
lib/                 Vendor libMVSDK.so, one per architecture (x64, arm64, ...)
demo/                Vendor example programs (C++, Python, Qt/GTK viewers)
tools/               Vendor GUI/CLI camera configuration utilities
document/            Vendor API reference manuals (CHM/PDF)

webapp/              Our application (this is what actually runs in production)
  app/
    main.py            FastAPI endpoints
    camera.py           CameraController: connects to the camera, records, snapshots
    mvsdk.py             ctypes wrapper around libMVSDK.so (vendor-provided, copied
                         from demo/python_demo/mvsdk.py)
  static/index.html    Control page (status, start/stop recording, snapshot, file list)
  Dockerfile
  requirements.txt

docker-compose.yml   Builds and runs webapp/ as a container
storage/             Recorded videos / snapshots land here (bind-mounted volume)
```

## Current status (as of this writing)

- Single-camera demo is built and deployed, reachable at
  `https://camera.satriamuda.cloud` (see [`PLAN.md`](PLAN.md) for why it's a
  subdomain and what runs where).
- No physical camera connected yet in production — the site correctly reports
  "camera not found" until one is on the network.
- No AI/analytics pipeline exists yet. This phase is scoped to **reliably
  recording and collecting video**, nothing more.

## Running it

```bash
docker compose up --build -d
```

Requires `--network host` in `docker-compose.yml` (already configured) —
GigE Vision camera discovery uses LAN UDP broadcast, which doesn't cross
Docker's default bridge network. This also means **the container must run on
a machine that is physically on the same local network as the camera** — see
`PLAN.md` for why that's a different machine than this docs' hosting VPS in
the real multi-field deployment.

Swap `SDK_LIB_DIR` in `docker-compose.yml` between `lib/x64` and `lib/arm64`
to match the host's CPU architecture.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/camera/status` | GET | Is a camera connected, is it recording |
| `/api/record/start` | POST | Start recording to a new file |
| `/api/record/stop` | POST | Stop the current recording |
| `/api/snapshot` | POST | Save a single still image |
| `/api/recordings` | GET | List saved files |
| `/api/recordings/{name}` | GET | Download a saved file |
