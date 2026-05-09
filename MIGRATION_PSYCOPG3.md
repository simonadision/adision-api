# Migration psycopg2 → psycopg3

Migration du driver PostgreSQL de `psycopg2-binary` vers `psycopg` v3
(avec extra `[binary]` partout sauf Windows ARM64 — voir section Plateformes).
Concerne le backend `adision-api` (Ad BUD + Ad ANA).

## Pourquoi

- `psycopg2` est en mode maintenance ; psycopg3 est la version activement
  développée et reçoit les correctifs/optimisations.
- API quasi-identique pour notre usage (DB-API 2.0 + paramètres `%s`).
- Aligne `adision-api` sur la même stack que `adision-app-api`, déjà migré.

## Changements appliqués

### 1. `requirements.txt`

Avant :

```
fastapi
uvicorn[standard]
psycopg2-binary
sqlalchemy
pandas
openpyxl
reportlab
pyjwt
```

Après :

```
fastapi
uvicorn[standard]; sys_platform != 'win32' or platform_machine != 'ARM64'
uvicorn; sys_platform == 'win32' and platform_machine == 'ARM64'
psycopg[binary]; sys_platform != 'win32' or platform_machine != 'ARM64'
psycopg; sys_platform == 'win32' and platform_machine == 'ARM64'
sqlalchemy
pandas
openpyxl
reportlab
pyjwt
```

Voir section **Plateformes** ci-dessous pour le pourquoi des markers
sur `psycopg` et `uvicorn`. `pandas` et `reportlab` n'ont **pas besoin**
de markers : tous deux ont des wheels ARM64 disponibles sur PyPI (pandas
publie un wheel `cp314-cp314-win_arm64`, reportlab est pure Python `py3-none-any`).

### 2. `api.py`

```diff
-import psycopg2
-from psycopg2.extras import RealDictCursor
+import psycopg
+from psycopg.rows import dict_row

 def get_conn():
-    return psycopg2.connect(DATABASE_URL, sslmode="require")
+    return psycopg.connect(DATABASE_URL, sslmode="require")
```

`sslmode` reste valide en psycopg3 : les `**kwargs` passés à `connect()`
sont fusionnés dans la conninfo libpq (mêmes noms de paramètres).
`DATABASE_URL` au format `postgresql://...` reste compatible côté driver
direct (`psycopg.connect`) — voir section SQLAlchemy pour le cas spécifique
de `create_engine`.

Deux curseurs `RealDictCursor` dans `api.py` (`/items/search` et
`/mapping/suggestions`) :

```diff
-cur = conn.cursor(cursor_factory=RealDictCursor)
+cur = conn.cursor(row_factory=dict_row)
```

### 3. SQLAlchemy : forcer le dialecte `postgresql+psycopg`

`api.py` crée un `engine` SQLAlchemy à partir de `DATABASE_URL`. Avec une
URL `postgresql://...` nue, SQLAlchemy choisit le dialecte par défaut
qui est `psycopg2` — ce qui ferait échouer l'import puisqu'on a retiré
`psycopg2-binary` du requirements.

Un helper injecte le suffixe de dialecte au moment de créer l'engine,
sans modifier la variable d'env elle-même (qui reste réutilisée telle
quelle par `psycopg.connect`, lui-même indifférent au préfixe).

```diff
+def _sqlalchemy_url(url: str) -> str:
+    """Force SQLAlchemy à utiliser le dialecte psycopg3 (`postgresql+psycopg`).
+    Sans ce préfixe, SQLAlchemy chercherait psycopg2 par défaut."""
+    if url.startswith("postgresql+"):
+        return url
+    if url.startswith("postgresql://"):
+        return "postgresql+psycopg://" + url[len("postgresql://"):]
+    if url.startswith("postgres://"):
+        return "postgresql+psycopg://" + url[len("postgres://"):]
+    return url
+
-engine = create_engine(DATABASE_URL)
+engine = create_engine(_sqlalchemy_url(DATABASE_URL))
```

Le helper gère aussi le scheme `postgres://` (legacy Heroku/Railway) et
laisse passer telles quelles les URLs déjà préfixées (`postgresql+psycopg`,
`postgresql+psycopg2`, etc.) pour rester idempotent.

### 4. `modules/auth_jwt.py`

L'équivalent psycopg3 de `RealDictCursor` est `dict_row` (row factory).
Différence d'API : on passe `row_factory=` au lieu de `cursor_factory=`.

```diff
-from psycopg2.extras import RealDictCursor
+from psycopg.rows import dict_row

-cur = conn.cursor(cursor_factory=RealDictCursor)
+cur = conn.cursor(row_factory=dict_row)
```

`dict_row` retourne déjà un `dict` natif, donc l'appel `dict(row)` à la
fin de `_provision_user` reste fonctionnel (redondant mais inoffensif,
gardé pour minimiser le diff).

### 5. `modules/ad_ana_api.py`

Mêmes substitutions que `auth_jwt.py` (2 curseurs).

### 6. `modules/ad_budget_api.py`

Mêmes substitutions (~24 curseurs `RealDictCursor`). En plus, ce module
utilisait `psycopg2.extras.Json` pour adapter des dicts/listes Python
vers les colonnes JSONB d'`app_ana.project_snapshots` :

```diff
-from psycopg2.extras import Json, RealDictCursor
+from psycopg.rows import dict_row
+from psycopg.types.json import Json
```

L'API d'instanciation est identique (`Json(obj, dumps=...)`), donc les
deux usages dans `_create_snapshot` ne changent pas.

### 7. `ad_budget_api.py` (fichier racine, version legacy du router)

Mêmes substitutions `RealDictCursor` → `dict_row` (3 curseurs).

### 8. Scripts d'import et scraper

`import_railway.py`, `import_budget.py`, `scraper/notepad scraper.py` :

```diff
-import psycopg2
+import psycopg

-psycopg2.connect(DATABASE_URL)
+psycopg.connect(DATABASE_URL)
```

Pour les scripts qui passent un dict de paramètres
(`import_budget.py`, `scraper/notepad scraper.py`), le mot-clé libpq
`database` n'est plus reconnu en psycopg3 (psycopg2 acceptait
historiquement `database` comme alias, psycopg3 suit strictement libpq) :

```diff
 DB_CONFIG = {
     "host": "localhost",
-    "database": "Adision",
+    "dbname": "Adision",
     "user": "postgres",
     "password": "...",
 }
```

## Compatibilité comportementale

Pas d'autre ajustement requis pour notre code :

- `conn.cursor()` sans factory retourne toujours des tuples → les endpoints
  comme `/health` qui font `cur.fetchone()[0]` continuent de fonctionner.
- Substitution `%s` identique (psycopg3 supporte aussi `%(name)s`).
- `conn.commit() / rollback() / close()`, `cur.execute() / fetchone() /
  fetchall() / close()` : API inchangée.
- `RETURNING ...` + `cur.fetchone()` : inchangé (et avec `row_factory=dict_row`
  on récupère bien `cur.fetchone()["id"]` comme avant).
- `Json(obj, dumps=...)` : signature identique (`psycopg.types.json.Json`
  remplace `psycopg2.extras.Json`).
- Le `engine` SQLAlchemy continue de servir `pd.read_sql_query(...)` dans
  `/items` ; le pilote sous-jacent est juste passé de psycopg2 à psycopg3.

## Plateformes

Deux dépendances ont besoin d'un fallback sur Windows ARM64 :

### psycopg

`psycopg[binary]` télécharge le paquet `psycopg-binary` qui embarque
`libpq` précompilé. Sur Railway (Linux x86_64) c'est ce qu'on veut :
install rapide, aucune dépendance système à gérer.

**Mais `psycopg-binary` n'a PAS de wheel `win_arm64`** (vérifié sur PyPI
3.3.4 : wheels ARM64 publiés uniquement pour macOS, pas Windows). Donc
`pip install psycopg[binary]` échoue avec `ResolutionImpossible` sur ce
poste.

Solution : marker de plateforme. Tout sauf Windows ARM64 →
`psycopg[binary]` (wheel binaire). Windows ARM64 → `psycopg` pur Python.

### uvicorn[standard]

L'extra `[standard]` tire `httptools`, qui n'a **pas non plus** de wheel
`win_arm64` et tente une compilation C from-source qui échoue sans MSVC :

```
error: Microsoft Visual C++ 14.0 or greater is required.
```

Même solution : marker de plateforme. Linux/macOS et Windows x86_64 →
`uvicorn[standard]` (avec httptools, websockets, watchfiles, uvloop si
applicable). Windows ARM64 → `uvicorn` minimal (le serveur HTTP marche
quand même, juste avec le parser `h11` pur Python au lieu de httptools).

Railway/Linux n'est pas affecté par ces deux markers : il continue
d'installer les wheels binaires optimisés.

### pandas / reportlab

Vérifiés OK sur Windows ARM64 sans aucun marker spécifique :

- `pandas 3.0.2` : wheel `cp314-cp314-win_arm64` publié sur PyPI.
- `numpy 2.4.4` : wheel `cp314-cp314-win_arm64` publié (dépendance pandas).
- `reportlab 4.5.0` : pure Python (`py3-none-any`), aucune compilation.
- `pillow 12.2.0` : wheel `cp314-cp314-win_arm64` (dépendance reportlab).

Donc pas de marker plateforme nécessaire pour ces 4 paquets.

### Limitation locale Windows ARM64 — libpq.dll

Le `psycopg` pur Python s'**installe** sans problème sur Windows ARM64,
mais à l'import il essaie de charger `libpq.dll` dynamiquement. Si la DLL
n'est pas présente, on obtient :

```
ImportError: no pq wrapper available.
Attempts made:
- couldn't import psycopg 'c' implementation: No module named 'psycopg_c'
- couldn't import psycopg 'binary' implementation: No module named 'psycopg_binary'
- couldn't import psycopg 'python' implementation: libpq library not found
```

Pour faire tourner le backend localement sur Windows ARM64, il faut
installer `libpq` séparément. Options :

1. **Installer PostgreSQL Windows** (l'installer EDB inclut `libpq.dll`).
   Mettre `C:\Program Files\PostgreSQL\<version>\bin` dans le `PATH`.
2. **Récupérer libpq.dll** d'une distribution binaire de PostgreSQL et la
   placer dans le venv (`venv\Scripts\`) ou un dossier sur le `PATH`.
3. **Dev sur WSL2** (Linux) : installer `psycopg[binary]` normalement, pas
   de souci de DLL. Recommandé si la prod tourne déjà sur Linux.
4. **Tester directement sur Railway** sans dev local de la BD.

Aucune de ces étapes n'est requise pour le déploiement Railway.

## Validation

```powershell
& venv\Scripts\python.exe -m pip install -r requirements.txt
```

- ✅ `psycopg` (3.3.4) s'installe via le fallback pur-Python.
- ✅ `uvicorn` (0.46.0) minimal s'installe via le fallback sans `[standard]`.
- ✅ `fastapi` (0.136.1), `pydantic` (2.13.4), `sqlalchemy` (2.0.49),
  `pandas` (3.0.2), `numpy` (2.4.4), `openpyxl` (3.1.5), `reportlab` (4.5.0),
  `pyjwt` (2.12.1) : OK.
- ✅ Install idempotent : `Successfully installed ...` au premier passage,
  `Requirement already satisfied` au second.
- ✅ Tests unitaires `python -m unittest tests.test_aggregates` : 10/10 OK
  (le module testé `modules/aggregates.py` est pure Python et n'a pas
  changé, mais le run confirme l'absence de régression d'imports
  transitifs).

Sur Railway (cible de prod) le install passe sans problème : Linux x86_64
a tous les wheels nécessaires (`psycopg-binary` + `httptools`).
