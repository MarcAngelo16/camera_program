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

- **A real camera (MindVision MV-GE232GC) is connected and working end to
  end**, reachable at `https://camera.satriamuda.cloud`. Verified: the site
  shows the camera as connected (product name + serial number), and
  start/stop recording + snapshot work against real hardware.
- The camera is plugged into a **Windows laptop** acting as the edge box (see
  [`PLAN.md`](PLAN.md) for why the camera-facing app has to run on a machine
  physically on the camera's network — this VPS can't reach it directly).
  The laptop runs `webapp/app` directly with `uvicorn` (not Docker — Docker
  Desktop on Windows can't do real host networking, and this repo only ships
  the Linux SDK binary anyway; Windows worked because the vendor's driver was
  already installed separately on that machine).
- The laptop and this VPS are linked over a **WireGuard tunnel**
  (server `10.10.0.1`, laptop `10.10.0.2`) so the VPS can reach the laptop's
  API despite it sitting behind a home/office router with no public IP.
- **The public page itself is hosted directly on this VPS**
  (`/var/www/camera.satriamuda.cloud/`, served by nginx), independent of the
  laptop — only `/api/...` calls are proxied over the tunnel to the laptop.
  This means the site always loads even if the laptop/tunnel is down; in that
  case the page shows "Camera service unreachable" instead of a raw gateway
  error or a stuck status. ("Camera service unreachable" = can't reach the
  laptop at all; "Camera not found" = laptop's app is running fine, just no
  camera plugged in — these are deliberately different messages.)
- **This is still a demo/proof-of-concept, not a durable deployment**: the
  laptop must stay powered on and awake with the `uvicorn` command running in
  an open terminal. Nothing here yet survives a reboot or a closed terminal —
  see `PLAN.md`'s open questions for what a real field deployment needs
  (an always-on edge box, the app running as a background service, etc).
- No AI/analytics pipeline exists yet. This phase is scoped to **reliably
  recording and collecting video**, nothing more.

## Running the edge app (the machine physically connected to the camera)

Two ways, depending on the OS of that machine:

**Linux** (recommended for a real deployment — matches this repo's Docker setup):
```bash
docker compose up --build -d
```
Requires `--network host` in `docker-compose.yml` (already configured) —
GigE Vision camera discovery uses LAN UDP broadcast, which doesn't cross
Docker's default bridge network. Swap `SDK_LIB_DIR` in `docker-compose.yml`
between `lib/x64` and `lib/arm64` to match the host's CPU architecture.

**Windows** (works if the vendor's camera driver/DLL is already installed on
that machine — this repo does not include a Windows SDK binary, only Linux):
```
pip install fastapi "uvicorn[standard]"
cd webapp
uvicorn app.main:app --host 0.0.0.0 --port 8001
```
Confirm it works locally first with `curl http://localhost:8001/api/camera/status`
before relying on any tunnel/remote access.

## Connecting a remote edge box to the public site

If the edge machine (laptop, mini-PC, etc.) isn't on the same network as
wherever the site is publicly hosted, link the two with a WireGuard tunnel
(what's currently deployed) and point nginx's `/api/` proxy at the edge
box's tunnel IP instead of `127.0.0.1`. See `PLAN.md` for the full reasoning
on why this split (edge box vs. central server) exists at all.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/camera/status` | GET | Is a camera connected, is it recording |
| `/api/record/start` | POST | Start recording to a new file |
| `/api/record/stop` | POST | Stop the current recording |
| `/api/snapshot` | POST | Save a single still image |
| `/api/recordings` | GET | List saved files |
| `/api/recordings/{name}` | GET | Download a saved file |
