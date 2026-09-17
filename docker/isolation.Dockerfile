# T11 isolation acceptance image: minimal, pinned, no repository content baked in.
# The acceptance runner mounts only the sandbox (rw) and the package source (ro).
FROM python:3.12-slim

RUN pip install --no-cache-dir "psycopg[binary]==3.2.12"

ENV PYTHONPATH=/opt/app/src
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
