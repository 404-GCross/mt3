FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    XLA_PYTHON_CLIENT_PREALLOCATE=false
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-tk libsndfile1 git ca-certificates \
    build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/mt3
COPY . .
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    PATH=/usr/bin:$PATH /opt/venv/bin/pip install "jax[cuda12]" && \
    PATH=/usr/bin:$PATH /opt/venv/bin/pip install -e .
ENV PATH=/opt/venv/bin:$PATH
ENTRYPOINT ["python", "gui/app.py"]
