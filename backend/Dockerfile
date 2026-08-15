FROM python:3.12-slim
WORKDIR /app
# ffmpeg: used by main.py's /sessions/{id}/export to concat per-turn video
# segments -- not needed by the worker's per-turn generation path (that
# stitching happens on the native imagepipe bridge), but backend and worker
# share this image, so it's here for the backend process specifically.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
