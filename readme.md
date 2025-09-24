# E-Inventário IFCE — Caucaia

## Requisitos
- Python 3.11+ (venv)
- Docker Desktop (para Postgres local) ou Postgres nativo
- Windows (dev) / Ubuntu (prod)

## Setup rápido (dev)
1. Criar venv, instalar dependências:
   - `pip install -r requirements.txt`
2. Subir Postgres (Docker) ou usar Postgres nativo.
3. Duplicar `.env.example` → `.env` e preencher `SECRET_KEY` e `DB_*`.
4. Rodar:
   - `python manage.py migrate`
   - `python manage.py createsuperuser`
   - `python manage.py runserver`
5. Acessar `/admin` (Jazzmin).

## Decisões e roadmap
Consulte o **MEMORIAL.md** (fonte de verdade).
