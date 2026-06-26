# syntax=docker/dockerfile:1
###############################################################################
# Vaastu-Lens room classifier — FastAPI + YOLOv8 inference service.
#
# Multi-stage build:
#   * builder  -> installs Python deps into a venv and pre-fetches the YOLO
#                 weights (so the container never downloads at runtime).
#   * runtime  -> slim image with just the venv, the app and the weights.
###############################################################################

ARG PYTHON_VERSION=3.11

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# OpenCV / ultralytics need a few native libs even for the headless build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the CPU-only torch wheels first (much smaller than the CUDA build),
# then the rest of the app requirements.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install -r requirements.txt

# Pre-download the YOLOv8m COCO weights so runtime is fully offline. The app
# expects them at the project root (MODEL_PATH = ROOT / "yolov8m.pt").
# ultralytics downloads into the CWD, so run from /opt to land at /opt/yolov8m.pt.
WORKDIR /opt
RUN python -c "from ultralytics import YOLO; YOLO('yolov8m.pt')" \
    && test -f /opt/yolov8m.pt

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # ultralytics wants a writable config dir; keep it inside the app tree.
    YOLO_CONFIG_DIR=/app/.ultralytics

# Runtime-only native libs for OpenCV headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

WORKDIR /app

# Bring over the prepared venv and the model weights.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/yolov8m.pt /app/yolov8m.pt

# Application code + rules database.
COPY scr/ ./scr/
COPY vastu_database_1/ ./vastu_database_1/

# Logs dir the classifier writes to.
RUN mkdir -p /app/logs /app/.ultralytics \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5004

# Container-level health check hits the app's own /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:5004/health || exit 1

# Serve with uvicorn. The app lives in scr/api.py exposing `app`.
CMD ["uvicorn", "scr.api:app", "--host", "0.0.0.0", "--port", "5004"]
