# --- Build stage: install deps into a venv, keep final image lean ---
FROM python:3.12-slim AS builder

WORKDIR /app

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Final stage ---
FROM python:3.12-slim

# Non-root user — don't run the app as root in the container.
# Explicit numeric UID/GID (not just a name) is required: Kubernetes'
# runAsNonRoot check can't resolve a named user against the image's
# /etc/passwd, and refuses to start the container with
# "cannot verify user is non-root" if all it has is a name.
RUN groupadd -r -g 10001 appuser && useradd -r -u 10001 -g appuser appuser

WORKDIR /app

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

COPY models.py database.py database_memory.py leave_service.py server.py ./

USER appuser

# streamable-http listens here when MCP_TRANSPORT=streamable-http (see server.py)
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

CMD ["python", "server.py"]