# Adision API — Backend Ad BUD

Backend FastAPI consommé par le frontend `ad-budget-client` (https://bud.adision.ca). Auth SSO partagée avec le reste de la suite Adision : le JWT est émis par `adision-app-api` (dashboard https://app.adision.ca) et vérifié localement (HS256 via `JWT_SECRET` partagé).

Déployé sur Railway à `https://web-production-3381d.up.railway.app` via auto-deploy sur push vers `main`.

## Stack

- FastAPI + Python 3.11 (cf. `runtime.txt`)
- PostgreSQL (Railway)
- PyJWT pour la vérification du JWT SSO
- Reportlab + openpyxl pour les exports PDF / Excel des budgets

## Setup local

1. **Créer un venv et installer les deps** (depuis la racine du repo) :

   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   # source .venv/bin/activate     # macOS / Linux
   pip install -r requirements.txt
   ```

2. **Copier `.env.example` en `.env` et remplir les valeurs** :

   ```bash
   cp .env.example .env
   # éditer .env
   ```

   Au minimum, `DATABASE_URL` doit pointer vers une Postgres locale (ou un dump de la prod). `JWT_SECRET` est optionnel en local — vide → les endpoints `/auth/me` et `/budget/*` répondront `401`, ce qui est le comportement attendu et n'empêche pas le serveur de démarrer.

   Les fichiers `.env`, `*-key.txt`, `*secret*.txt` etc. sont gitignorés. **Ne jamais commit de secret en clair.**

3. **Lancer le serveur** :

   ```bash
   uvicorn api:app --reload
   ```

4. **Smoke test** :

   ```bash
   curl http://localhost:8000/health
   ```

   Doit retourner `status: ok` et `jwt_secret_length: 64` si `JWT_SECRET` est rempli (sinon `0`).

## Variables d'environnement

| Variable             | Local | Railway (prod)                  | Description                                                       |
|----------------------|:-----:|:-------------------------------:|-------------------------------------------------------------------|
| `DATABASE_URL`       | ✅    | ✅ (auto-injectée par le service) | URL Postgres                                                       |
| `JWT_SECRET`         | optionnel | ✅ (manuelle)                | Secret HS256 — **identique** sur `adision-app-api` et `adision-viu-api` |
| `ANTHROPIC_API_KEY`  | optionnel (scraper) | ❌                | Clé Anthropic — utilisée seulement par `scraper/`, pas en prod    |
| `SERPAPI_KEY`        | optionnel (scraper) | ❌                | Clé SerpAPI — utilisée seulement par `scraper/`, pas en prod      |

La liste de référence est dans `.env.example`.

## Déploiement

Auto-deploy via Railway sur push vers `main`. `Procfile` lance `uvicorn api:app`. `_ensure_schema()` (lifespan startup hook dans `api.py`) applique les `ALTER TABLE … ADD COLUMN IF NOT EXISTS` au démarrage — idempotent, sûr en prod.

## Structure

```
api.py                      # FastAPI entry — /, /health, /auth/me + items/mapping
modules/
  ad_budget_api.py          # Router /budget/* (projets, lignes, admin items, push from-viu)
  auth_jwt.py               # Vérification JWT SSO + auto-provisioning ad_budget.users
.env.example                # Template des variables d'env
.gitignore                  # Inclut secrets, venvs, pyc
Procfile                    # Railway start command
requirements.txt            # Deps Python
runtime.txt                 # Pin version Python (Railway)

scraper/                    # Module local NON déployé. Code actuel incomplet
                            # (helpers manquants). Voir .env.example pour les
                            # clés à fournir si tu le réactives.
```
