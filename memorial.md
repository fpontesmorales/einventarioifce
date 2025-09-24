# E-Inventário IFCE — Caucaia
**Objetivo:** Sistema para conferência e gestão dos bens patrimoniais do IFCE Campus Caucaia, com importação direta do SUAP (CSV) e módulo de vistorias.

---

## 1) Escopo atual (fase em andamento)
- **Fase 1:** Base Django pronta para ambiente local e produção (Docker) com:
  - Admin em PT-BR, tema **Jazzmin** e branding do projeto.
  - Estrutura de apps definida (sem lógica pesada ainda).
  - **Configuração de banco “dual”**: SQLite por padrão (desenvolvimento), **Postgres por `.env`** (produção/servidor web).
  - Documentação viva (este memorial).

---

## 2) Decisões congeladas
- **Arquitetura:** Django monolítico com um **`settings.py` orientado por variáveis de ambiente** (`.env`). Sem split de settings; o mesmo arquivo serve para dev e prod (Docker).
- **Banco de dados:**
  - Dev: **SQLite** por padrão (zero atrito).
  - Prod: **PostgreSQL** (via `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`).
  - Migração entre bancos orientada por `.env` (sem alterações de código).
- **Formato de importação:** **CSV UTF-8** exportado direto do SUAP (sem passar pelo Excel).
- **Locale:** `pt-br` e fuso `America/Fortaleza`.
- **Admin:** **Jazzmin** como tema padrão; branding “E-Inventário IFCE — Caucaia”.
- **Vistoria:** **Aplicação e rota própria, fora do Admin.**
  - Motivos: simplicidade operacional, tela limpa para busca/checagem em campo, menor fricção em dispositivos móveis.
  - O Admin terá **atalhos** para a página pública de Vistoria.
- **Documentação:** este `MEMORIAL.md` é **fonte de verdade**. Toda mudança arquitetural passa por aqui **antes** de ser implementada.

---

## 3) Estrutura de pastas (alvo)
