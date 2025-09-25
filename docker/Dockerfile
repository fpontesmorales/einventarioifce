FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# deps do Pillow (runtime) e do sistema
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g libfreetype6 libtiff6 libwebp7 libopenjp2-7 liblcms2-2 \
    && rm -rf /var/lib/apt/lists/*

# requirements
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# código do projeto
COPY . /app

# pastas de static/media (montadas também via volumes)
RUN mkdir -p /app/staticfiles /app/media

EXPOSE 8000
