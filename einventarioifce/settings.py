import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def env_bool(key: str, default: str = "0") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes", "on")

# -----------------------------------------------------------------------------
# Django básico
# -----------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = env_bool("DEBUG", "0")

# Hosts (lê do env; fallback seguro p/ intranet + domínio)
_hosts_env = os.getenv("ALLOWED_HOSTS") or os.getenv("DJANGO_ALLOWED_HOSTS")
if _hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _hosts_env.split(",") if h.strip()]
else:
    ALLOWED_HOSTS = ["10.10.2.46", "localhost", "127.0.0.1", "einventario.morales.dev.br"]

# CSRF (Django 5+ exige origem com esquema)
_csrf_env = os.getenv("CSRF_TRUSTED_ORIGINS")
if _csrf_env:
    CSRF_TRUSTED_ORIGINS = [u.strip() for u in _csrf_env.split(",") if u.strip()]
else:
    CSRF_TRUSTED_ORIGINS = [
        "http://10.10.2.46",
        "http://localhost",
        "http://127.0.0.1",
        "https://einventario.morales.dev.br",
    ]

# Proxy (Traefik/Nginx)
USE_X_FORWARDED_HOST = env_bool("USE_X_FORWARDED_HOST", "1")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Cookies/redirect controlados por ENV (não force “secure” só por DEBUG=0)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", "0")
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", "0")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", "0")

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
    "relatorios",
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
# Banco de dados (usa DATABASE_URL se presente; senão DB_*/POSTGRES_*/PG*)
# -----------------------------------------------------------------------------
def _env(*keys, default=None):
    """Retorna o primeiro valor de env que existir e não for '', senão default."""
    for k in keys:
        v = os.getenv(k)
        if v not in (None, ""):
            return v
    return default

# 1) Preferir variáveis explícitas (funciona no Dokploy e no .env local)
DB_NAME = _env("DB_NAME", "POSTGRES_DB")
DB_USER = _env("DB_USER", "POSTGRES_USER")
DB_PASSWORD = _env("DB_PASSWORD", "POSTGRES_PASSWORD", "PGPASSWORD", default="")
DB_HOST = _env("DB_HOST", "PGHOST")
DB_PORT = _env("DB_PORT", "PGPORT", default="5432")

if DB_NAME and DB_USER and DB_HOST:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": DB_NAME,
            "USER": DB_USER,
            "PASSWORD": DB_PASSWORD,
            "HOST": DB_HOST,
            "PORT": DB_PORT,
            "CONN_MAX_AGE": int(_env("DB_CONN_MAX_AGE", default="60")),
        }
    }
else:
    # 2) Se não veio DB_*, tentar DATABASE_URL (opcional; requer dj-database-url)
    DATABASE_URL = os.getenv("DATABASE_URL")
    if DATABASE_URL:
        try:
            import dj_database_url  # opcional; instale apenas se quiser usar DATABASE_URL
            DATABASES = {
                "default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)
            }
        except Exception:
            # Se não tiver a lib, cai no fallback abaixo
            pass

# 3) Fallback dev (não recomendado para produção; só para não quebrar o runserver)
if "DATABASES" not in globals():
    BASE_DIR = Path(__file__).resolve().parent.parent  # ajuste se diferente
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
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
STATIC_ROOT = Path(os.getenv("STATIC_ROOT", BASE_DIR / "staticfiles"))

STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "media"))

# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------
LOGIN_URL = "/admin/login/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -----------------------------------------------------------------------------
# Jazzmin
# -----------------------------------------------------------------------------
JAZZMIN_SETTINGS = {
    "site_title": "E-Inventário IFCE",
    "site_header": "E-Inventário IFCE",
    "welcome_sign": "Bem-vindo ao E-Inventário",
    "show_ui_builder": False,

    # Mantém a navegação aberta
    "navigation_expanded": True,

    # Atalhos no topo
    "topmenu_links": [
        {"name": "Relatório Final", "url": "relatorios:final", "new_window": False},
        {"name": "Relatório Operacional", "url": "relatorios:operacional", "new_window": False},
        {"name": "Exportar fotos", "url": "relatorios:exportar_fotos", "new_window": False},
    ],

    # Links extras sob o app "relatorios" na sidebar
    "custom_links": {
        "relatorios": [
            {"name": "Relatório Final", "url": "relatorios:final", "icon": "fas fa-print"},
            {"name": "Relatório Operacional", "url": "relatorios:operacional", "icon": "fas fa-list-check"},
            {"name": "Exportar fotos", "url": "relatorios:exportar_fotos", "icon": "fas fa-file-archive"},
        ],
    },

    "icons": {
        "relatorios.RelatorioConfig": "fa fa-cog",
        "vistoria.Inventario": "fa fa-clipboard-check",
        "vistoria.VistoriaBem": "fa fa-check-square",
        "patrimonio.Bem": "fa fa-cube",
    },
}
JAZZMIN_UI_TWEAKS = {"theme": "yeti"}
