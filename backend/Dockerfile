# Figion backend.
#
# Two stages: a builder that compiles wheels, and a slim runtime that carries none of the
# toolchain. argon2-cffi and asyncpg both build native extensions, so gcc has to exist
# somewhere — just not in the shipped image.

# ── Builder ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependency metadata first: this layer is cached until pyproject.toml itself changes, so
# editing application code does not trigger a recompile of asyncpg and argon2.
COPY pyproject.toml ./
COPY app/__init__.py ./app/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip \
    && pip install '.[onnx]'

# ── Runtime ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# opencv-python-headless still needs libGL's ABI companions for a few codecs, but not the X11
# stack the GUI build drags in — which is the whole reason the headless wheel is pinned in
# pyproject.toml. Installing plain opencv-python here would fail at import inside a container.
# fonts-dejavu-core: ReportLab's built-in fonts are Latin-1, which lacks the Turkish ğ, ı, ş
# and İ. Without a Unicode TTF those render as blanks in the PDF a farmer hands to a buyer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        curl \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 10001 figion \
    && chown -R figion:figion /srv

# Unprivileged: the process decodes attacker-supplied JPEGs, so a decoder bug should not land
# on root.
USER figion

EXPOSE 8000

# Liveness only — readiness depends on the database, which Docker's healthcheck should not be
# gating container restarts on. Orchestrators point their readiness probe at /health/ready.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# One worker on purpose. The model is loaded once per process, so a second worker doubles its
# memory for no throughput gain — inference is already spread across threads inside the
# process. Scale with replicas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
