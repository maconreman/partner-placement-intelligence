# ── Stage 1: Build Next.js static export ─────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python FastAPI server ────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Model cache lives inside /app so the non-root user can read it
ENV HF_HOME=/app/.cache/huggingface

# Install Python deps
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# M9.4: bake the embedding model into the image at build time, so a restart
# never downloads it and no Inference credits are used at run time.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-base-en-v1.5', device='cpu')"
ENV HF_HUB_OFFLINE=1

# Copy backend
COPY backend/ ./backend/

# Copy built frontend
COPY --from=frontend-builder /build/frontend/out ./frontend/out

# HF Spaces runs as non-root user 1000
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser

# HF Spaces default port
EXPOSE 7860

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
