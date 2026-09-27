import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .camera import CameraController, CameraNotReady

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


@app.post("/api/record/start")
def record_start():
    try:
        filename = controller.start_recording()
    except CameraNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"recording": True, "file": filename}


@app.post("/api/record/stop")
def record_stop():
    try:
        filename = controller.stop_recording()
    except CameraNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"recording": False, "file": filename}


@app.post("/api/snapshot")
def snapshot():
    try:
        filename = controller.snapshot()
    except CameraNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"file": filename}


@app.get("/api/recordings")
def list_recordings():
    entries = []
    for name in sorted(os.listdir(STORAGE_DIR)):
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


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
