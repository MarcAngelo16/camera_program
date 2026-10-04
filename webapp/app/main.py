import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .camera import CameraController, CameraNotReady
from .transcode import is_transcode_enabled, segment_history, set_transcode_enabled
from .upload import last_upload_stats, upload_async

# Default layout: webapp/app/main.py -> webapp/storage, webapp/static.
# In the Docker image this is /app/app/main.py -> /app/storage, /app/static,
# matching the Dockerfile's ENV STORAGE_DIR=/app/storage (which still wins
# when set, e.g. inside the container).
WEBAPP_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = os.environ.get("STORAGE_DIR", str(WEBAPP_DIR / "storage"))
STATIC_DIR = str(WEBAPP_DIR / "static")

app = FastAPI(title="Camera Control")
controller = CameraController(storage_dir=STORAGE_DIR)


@app.get("/api/camera/status")
def camera_status():
    return controller.status()


@app.get("/api/camera/stats")
def camera_stats():
    stats = controller.stats()
    stats["last_upload"] = last_upload_stats()
    stats["segment_history"] = segment_history()
    return stats


@app.get("/api/camera/transcode")
def get_transcode():
    return {"transcode_enabled": is_transcode_enabled()}


class TranscodeRequest(BaseModel):
    enabled: bool


@app.post("/api/camera/transcode")
def set_transcode(req: TranscodeRequest):
    set_transcode_enabled(req.enabled)
    return {"transcode_enabled": is_transcode_enabled()}


@app.get("/api/camera/resolutions")
def camera_resolutions():
    return {"presets": controller.resolution_presets()}


class ResolutionRequest(BaseModel):
    index: int


@app.post("/api/camera/resolution")
def set_resolution(req: ResolutionRequest):
    try:
        controller.set_resolution(req.index)
    except CameraNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return controller.status()


@app.post("/api/record/start")
def record_start():
    try:
        session_id = controller.start_recording()
    except CameraNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"recording": True, "session_id": session_id}


@app.post("/api/record/stop")
def record_stop():
    try:
        session_id = controller.stop_recording()
    except CameraNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # Each chunk is transcoded + uploaded on its own as it closes (including
    # the final one, closed inside stop_recording) -- nothing to upload here.
    return {"recording": False, "session_id": session_id}


@app.post("/api/snapshot")
def snapshot():
    try:
        filename = controller.snapshot()
    except CameraNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
    upload_async(os.path.join(STORAGE_DIR, filename), filename)
    return {"file": filename}


@app.get("/api/recordings")
def list_recordings():
    entries = []
    for name in sorted(os.listdir(STORAGE_DIR)):
        if name.startswith("."):
            continue
        path = os.path.join(STORAGE_DIR, name)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        entries.append({"name": name, "size_bytes": stat.st_size, "modified": stat.st_mtime})
    return {"files": entries}


@app.get("/api/recordings/{name}")
def download_recording(name: str):
    if "/" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = os.path.join(STORAGE_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, filename=name)


@app.delete("/api/recordings/{name}")
def delete_recording(name: str):
    if "/" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = os.path.join(STORAGE_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found")
    os.remove(path)
    return {"deleted": name}


@app.delete("/api/recordings")
def delete_all_recordings():
    deleted = []
    for name in os.listdir(STORAGE_DIR):
        if name.startswith("."):
            continue
        path = os.path.join(STORAGE_DIR, name)
        if os.path.isfile(path):
            os.remove(path)
            deleted.append(name)
    return {"deleted": deleted}


@app.post("/api/upload")
async def upload_recording(file: UploadFile = File(...)):
    """Receive a finished file pushed from an edge box (see app/upload.py).

    Not used on the edge box itself (UPLOAD_URL there points elsewhere) —
    this only matters on whichever instance is acting as central storage.
    """
    if not file.filename or "/" in file.filename or file.filename in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid filename")
    dest = os.path.join(STORAGE_DIR, file.filename)
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)
    return {"saved": file.filename}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
