FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/requirements-cpu.txt ./requirements.txt
RUN python -m pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 iris

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=iris:iris backend ./backend
COPY --chown=iris:iris run_backend.py ./run_backend.py
RUN mkdir -p /app/database /home/iris/.insightface \
    && chown -R iris:iris /app/database /home/iris/.insightface

USER iris
EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=10m --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=4)"

CMD ["python", "run_backend.py", "--no-reload"]
