"""
FastAPI wrapper around the room classifier.

Endpoints:
    GET  /health         -> { "ok": true, "device": "...", "model": "..." }
    GET  /languages      -> { code: DisplayName } of supported rule languages
    GET  /logs           -> recent classification log entries (newest first)
    POST /classify       -> JSON result (room + localized rules) for an image
    POST /classify/image -> annotated JPEG (image/jpeg) for the uploaded image

Run:
    uvicorn api:app --host 0.0.0.0 --port 5015 --reload
    # or simply:  python api.py
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

# Make the local modules importable whether this file is run directly
# (python scr/api.py) or as a package (uvicorn scr.api:app) from the root.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vastu_rules
from vaastu_object_detection import (
    CONF_TH,
    LOG_FILE,
    MODEL_PATH,
    classify_image,
    load_model,
)

import logging


class _HealthAccessFilter(logging.Filter):
    """Drop uvicorn access-log lines for the /health probe.

    The container HEALTHCHECK and the on-host deploy check poll /health
    frequently; logging every probe floods CloudWatch. Real request paths
    (/classify, /languages, ...) are still logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn.access args: (client_addr, method, path, http_version, status)
        if args and len(args) >= 3 and str(args[2]).startswith("/health"):
            return False
        return True


logging.getLogger("uvicorn.access").addFilter(_HealthAccessFilter())


app = FastAPI(
    title="Vaastu Room Classifier",
    description="Upload a photo → get the inferred room type (master_bedroom, "
                "living_room, kitchen, bathroom, dining_room).",
    version="1.0.0",
)

MODEL = None  # populated on startup


@app.on_event("startup")
def _startup() -> None:
    global MODEL
    MODEL = load_model(MODEL_PATH)


def _read_upload_to_tempfile(upload: UploadFile) -> Path:
    """Persist an uploaded image to a temp file so classify_image can open it."""
    suffix = Path(upload.filename or "upload").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        raise HTTPException(415, f"Unsupported image type: {suffix}")

    data = upload.file.read()
    if not data:
        raise HTTPException(400, "Empty upload")

    # Validate it's a real decodable image before handing off.
    arr = np.frombuffer(data, dtype=np.uint8)
    if cv2.imdecode(arr, cv2.IMREAD_COLOR) is None:
        raise HTTPException(400, "Uploaded file is not a readable image")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()
    return Path(tmp.name)


@app.get("/health")
def health() -> dict:
    import torch
    return {
        "ok": MODEL is not None,
        "device": "cuda:0" if torch.cuda.is_available() else "cpu",
        "model": Path(MODEL_PATH).name,
    }


@app.get("/languages")
def languages() -> list:
    """Supported rule language names, e.g. ["English", "Telugu", "Hindi", ...]."""
    return vastu_rules.available_languages()


@app.get("/logs")
def logs(limit: int = 50) -> JSONResponse:
    """Return the most recent classification log entries, newest first.

    Reads logs/classifications.jsonl written by the classifier. `limit` caps
    how many entries come back (default 50; pass limit<=0 for all of them).
    """
    if not LOG_FILE.exists():
        return JSONResponse({"count": 0, "entries": []})

    entries = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip any corrupt line, never break the endpoint

    entries.reverse()  # newest first
    if limit and limit > 0:
        entries = entries[:limit]
    return JSONResponse({"count": len(entries), "entries": entries})


@app.post("/classify")
async def classify(
    file: UploadFile = File(..., description="Image of a room"),
    language: str = Form("English", description="Rules language name: English, Telugu, Hindi, ..."),
    conf: float = Form(CONF_TH),
) -> JSONResponse:
    """Return the JSON classification result + localized rules (no annotated image)."""
    if MODEL is None:
        raise HTTPException(503, "Model not loaded yet")

    img_path = _read_upload_to_tempfile(file)
    try:
        result = classify_image(MODEL, img_path, conf=conf, language=language)
    finally:
        img_path.unlink(missing_ok=True)

    result.pop("image", None)  # drop the temp path from the response
    return JSONResponse(result)


@app.post("/classify/image")
async def classify_with_image(
    file: UploadFile = File(..., description="Image of a room"),
    language: str = Form("English", description="Rules language name: English, Telugu, Hindi, ..."),
    conf: float = Form(CONF_TH),
) -> StreamingResponse:
    """Return the annotated JPEG directly (room badge + boxes drawn on the photo)."""
    if MODEL is None:
        raise HTTPException(503, "Model not loaded yet")

    img_path = _read_upload_to_tempfile(file)
    out_path = img_path.with_name(f"annotated_{img_path.name}")
    try:
        result = classify_image(MODEL, img_path, conf=conf, save_path=out_path,
                                language=language)
        annotated_bytes = out_path.read_bytes()
    finally:
        img_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)

    headers = {
        "X-Room": result["room"],
        "X-Room-Confidence": str(result["room_confidence"]),
    }
    return StreamingResponse(
        io.BytesIO(annotated_bytes),
        media_type="image/jpeg",
        headers=headers,
    )


if __name__ == "__main__":
    import uvicorn
    # Pass the app object directly so a plain `python scr/api.py` works without
    # needing a module import string (reload mode would require the latter).
    uvicorn.run(app, host="0.0.0.0", port=5004)
