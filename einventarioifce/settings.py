import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# Django básico
# -----------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.getenv("DEBUG", "0") == "1"

ALLOWED_HOSTS = [h.strip() for h in os.getenv(
    "ALLOWED_HOSTS",
    "einventario.morales.dev.br,localhost,127.0.0.1"
).split(",") if h.strip()]

# Necessário com esquema (https://) para CSRF no Django 5+
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv(
    "CSRF_TRUSTED_ORIGINS",
    "https://einventario.morales.dev.br"
).split(",") if o.strip()]

# Quando estiver atrás de proxy (Cloudflare → Nginx)
USE_X_FORWARDED_HOST = os.getenv("USE_X_FORWARDED_HOST", "1") == "1"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Cookies e redirect seguro apenas em produção
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = False  # Cloudflare lida com HTTPS na borda

# -----------------------------------------------------------------------------
# Apps
# -----------------------------------------------------------------------------
INSTALLED_APPS = [
    "jazzmin",

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

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

# -----------------------------------------------------------------------------
# Banco de dados (Postgres por padrão; usa .env)
# -----------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "einventario"),
        "USER": os.getenv("DB_USER", "einventario"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

# -----------------------------------------------------------------------------
# Locale / TZ
# -----------------------------------------------------------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.getenv("TIME_ZONE", "America/Fortaleza")
USE_I18N = True
USE_TZ = True

# -----------------------------------------------------------------------------
# Static / Media (Nginx serve /static e /media)
# -----------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------
LOGIN_URL = "/admin/login/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -----------------------------------------------------------------------------
# Jazzmin (tema Yeti, para evitar o aviso do 'light')
# -----------------------------------------------------------------------------
JAZZMIN_SETTINGS = {
    "site_title": "E-Inventário IFCE",
    "site_header": "E-Inventário IFCE",
    "welcome_sign": "Bem-vindo ao E-Inventário",
    "show_ui_builder": False,
}
JAZZMIN_UI_TWEAKS = {
    "theme": "yeti",         # claro
    #"dark_mode_theme": "darkly",  # opcional para dark mode
}

# -----------------------------------------------------------------------------
# Segurança adicional opcional (ajuste conforme necessidade)
# -----------------------------------------------------------------------------
# X_FRAME_OPTIONS = "DENY"
# SECURE_CONTENT_TYPE_NOSNIFF = True
# SECURE_BROWSER_XSS_FILTER = True
# CSRF_COOKIE_HTTPONLY = False  # True pode atrapalhar admin; deixe False
# SESSION_COOKIE_HTTPONLY = True
