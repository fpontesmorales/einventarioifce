# docker/Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependências em runtime para Pillow (como você já tinha)
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g libfreetype6 libtiff6 libwebp7 libopenjp2-7 liblcms2-2 \
 && rm -rf /var/lib/apt/lists/*

# Requisitos do projeto
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir gunicorn psycopg2-binary

# Código do projeto
COPY . /app

# Pastas para static/media (também montadas via volumes)
RUN mkdir -p /app/staticfiles /app/media

EXPOSE 8000
