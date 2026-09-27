# Project Plan: Padel Camera Recording System

## Problem

Padel fields each get a camera. A player at a field scans a QR code specific
to that field, and can start/stop a recording of their match. The video needs
to end up somewhere central so it can be used later — first just to exist as
a video the player can retrieve, later as training data for an AI pipeline
(match analytics, highlights, tracking — not yet designed).

This phase's scope is deliberately narrow: **get recording + collection
working reliably across multiple fields.** No AI/analytics yet.

## Why this isn't a single-server problem

The camera SDK (MindVision MVSDK) talks to GigE Vision cameras over local
network discovery (UDP broadcast) — it only works from a machine that is
physically on the same LAN as the camera. A cloud VPS with just a public IP
cannot reach a camera sitting on a padel field's local network, no matter how
Docker networking is configured.

So every field needs its own **edge box** — a small on-site machine
(initially any spare PC/mini-PC; later possibly the GPU mini-PC once bought)
physically wired (RJ45) to that field's camera. The edge box is the only
thing that ever calls the camera SDK directly.

## Architecture (current phase)

```
Padel Field A                          Padel Field B
┌─────────────┐                        ┌─────────────┐
│ Camera (GigE)│                        │ Camera (GigE)│
└──────┬──────┘                        └──────┬──────┘
       │ RJ45 (local LAN)                     │ RJ45 (local LAN)
┌──────▼──────────────┐                ┌──────▼──────────────┐
│ Edge box A            │                │ Edge box B            │
│ - runs webapp/ container│              │ - runs webapp/ container│
│ - talks to camera via SDK│             │ - talks to camera via SDK│
│ - records to local disk │              │ - records to local disk │
│ - Tailscale peer         │              │ - Tailscale peer         │
└──────────┬───────────┘                └──────────┬───────────┘
           │ Tailscale (NAT-proof private mesh)     │
           └───────────────┬─────────────────────────┘
                            ▼
                 Central server (this VPS)
                 - nginx: one route per field (like
                   camera.satriamuda.cloud / patent.satriamuda.cloud
                   today), proxying to that field's edge box over
                   its Tailscale IP
                 - QR code per field → https://<field-slug>.satriamuda.cloud
                 - receives uploaded recordings from edge boxes for
                   central storage (once a recording finishes)
```

### Why Tailscale (not port-forwarding)

Most venues won't give us control of their router, and residential/business
connections rarely have a stable public IP. Tailscale (or plain WireGuard)
creates a private mesh between each edge box and the central server without
needing any inbound port opened on the field's network — the edge box makes
an outbound connection out, same as any other internet client.

### Why static nginx routes per field (not a dynamic proxy service)

We already have working, understood infrastructure for "one subdomain → one
backend" (`camera.satriamuda.cloud`, `patent.satriamuda.cloud`). For a
handful of fields, adding one more DNS record + one more nginx block per new
field is simple and reuses what's already proven. This does **not** scale
past roughly a dozen fields gracefully (manual per-field config) — see "Later"
below for the upgrade path.

## QR code flow

1. Each field gets a slug, e.g. `field1`.
2. DNS + nginx get a route for `field1.satriamuda.cloud` (or a path — TBD,
   see open questions), proxying to that field's edge box's Tailscale IP.
3. The QR code printed/posted at the field encodes that URL.
4. Scanning it opens the same control page we built (`webapp/static/index.html`)
   — status, start/stop recording, snapshot — for that specific field's camera.

## Data collection

- Recordings are written to local disk on the edge box during the match
  (matches this phase's existing `CameraInitRecord`/`CameraPushFrame`/
  `CameraStopRecord` flow — no changes needed there).
- Recording locally first (not streaming to the central server live) means a
  flaky internet link at the field doesn't lose the recording — only the
  later upload step needs connectivity, and it can retry.
- Once a recording finishes, the edge box uploads the finished file to the
  central server (or, once it exists, directly to the GPU mini-PC — see
  below). This should be a simple, swappable upload target (an env var:
  "where do finished recordings get sent") so switching the destination later
  requires no code changes on the edge box.
- Central storage should keep enough metadata per file — field id, camera
  serial number, start/end timestamp — for later work (AI pipeline, or just
  finding "the recording from field 2 on Tuesday at 4pm") without needing to
  parse video files themselves.

## Later phase: GPU mini-PC + AI pipeline

Once the AI pipeline (currently in development, out of scope here) is ready:

- A GPU-equipped mini-PC gets bought and becomes the ingestion target for
  recordings — either replacing the central VPS as the upload destination, or
  sitting alongside it as a consumer of the same central store.
- Because the upload target is already just a config value (see above), this
  is a deployment change, not a code change on the edge side.
- The AI pipeline's actual design (what it does with the video) is
  intentionally not decided here — this plan only guarantees the video data
  will already exist, be organized by field/camera/time, and be reachable by
  whatever the pipeline turns out to need.

## Open questions / decisions still needed

- **Field routing shape**: subdomain per field (`field1.satriamuda.cloud`) vs.
  path per field (`satriamuda.cloud/field1`) vs. something else. Subdomains
  match our existing pattern most closely; paths might read better on a
  printed QR code. Not decided yet.
- **Multiple cameras per field**: today's `CameraController` assumes exactly
  one camera. If a field ever needs 2+ cameras, `camera.py` needs to pick a
  specific device (by serial number) instead of "the first one found."
- **Recording trigger UX for a real match**: today's control page requires a
  manual Start/Stop click. Worth deciding later whether a padel session has a
  more natural trigger (e.g. a fixed duration, or a physical start button at
  the field) instead of relying on someone tapping a web button correctly.
- **Player video retrieval**: current plan collects data centrally, but
  doesn't yet define how the player who recorded gets *their* video back
  (email? a link in the QR page after stopping? not scoped yet).
- **Onboarding a new field**: currently a manual process (add DNS record, add
  nginx block, add Tailscale peer, deploy container to a new edge box). Worth
  scripting once there are enough fields that doing this by hand is a real
  cost.

## Status right now

- [x] Single-camera demo app (`webapp/`) built, containerized, and deployed
      at `https://camera.satriamuda.cloud` for testing without hardware.
- [ ] Edge box provisioned at an actual field.
- [ ] Tailscale (or equivalent) mesh between an edge box and this server.
- [ ] Per-field nginx routing + QR code.
- [ ] Upload-on-finish from edge box to central storage.
- [ ] Central storage/metadata layout defined.
- [ ] AI pipeline (separate effort, in development).
