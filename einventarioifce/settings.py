from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# --- Segurança / Debug ---
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-override-me")
DEBUG = os.getenv("DEBUG", "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

# --- Apps ---
INSTALLED_APPS = [
    "jazzmin",  # tema do admin (pacote PyPI: django-jazzmin)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Apps do projeto (já criados)
    "core",
    "patrimonio",
    "importacao",
    "vistoria",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "einventarioifce.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "einventarioifce.wsgi.application"

# --- Banco de dados: PostgreSQL via .env ---
DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.postgresql")
if DB_ENGINE != "django.db.backends.postgresql":
    raise RuntimeError("Este projeto usa PostgreSQL. Ajuste DB_ENGINE no .env se necessário.")

DATABASES = {
    "default": {
        "ENGINE": DB_ENGINE,
        "NAME": os.getenv("DB_NAME", "einventario"),
        "USER": os.getenv("DB_USER", "einventario"),
        "PASSWORD": os.getenv("DB_PASSWORD", "password"),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,  # 1 min de pool
    }
}

# --- Localização / Fuso ---
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Fortaleza"
USE_I18N = True
USE_TZ = True

# --- Arquivos estáticos e mídia ---
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Django Admin / Jazzmin ---
JAZZMIN_SETTINGS = {
    "site_title": "E-Inventário IFCE — Caucaia",
    "site_header": "E-Inventário IFCE",
    "site_brand": "IFCE Caucaia",
    "welcome_sign": "Bem-vindo ao E-Inventário",
    "copyright": "IFCE Campus Caucaia",
}
JAZZMIN_UI_TWEAKS = {
    "theme": "light",
}

# --- Senhas / Auth ---
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
