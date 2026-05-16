from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from modules.ad_ana_api import register_ad_ana_routes
from modules.ad_budget_api import register_ad_budget_routes
from modules.taux_horaires_api import register_taux_horaires_routes
from modules.auth_jwt import make_jwt_deps
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:6268605Ss@localhost:5432/Adision")


def _sqlalchemy_url(url: str) -> str:
    """Force SQLAlchemy à utiliser le dialecte psycopg3 (`postgresql+psycopg`).
    Sans ce préfixe, SQLAlchemy chercherait psycopg2 par défaut."""
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


engine = create_engine(_sqlalchemy_url(DATABASE_URL))


def _is_local(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def get_conn():
    if _is_local(DATABASE_URL):
        return psycopg.connect(DATABASE_URL)
    return psycopg.connect(DATABASE_URL, sslmode="require")


def _ensure_schema():
    """Migrations idempotentes appliquées au démarrage. ADD COLUMN IF NOT EXISTS
    est sûr en prod : pas de réécriture de table, pas de blocage des requêtes
    en cours sur Postgres ≥ 11.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()
        # Import depuis Ad VIU : source_file = traçabilité + idempotence du push.
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS source_file TEXT"
        )
        # type_source ('soumission' | 'plan' | 'devis') : permet de distinguer
        # un re-push d'un même code_csi venant d'un autre type d'analyse, et
        # sert de clé d'idempotence supplémentaire.
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS type_source TEXT"
        )
        # Ventilation tri-axiale : prix_unitaire = matériel ; les colonnes
        # ci-dessous ajoutent la main-d'œuvre (heures × taux) et le
        # sous-traitant (montant + nom pour autocomplétion).
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS heures NUMERIC(10,2) DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS taux_horaire NUMERIC(10,2) DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS cout_sous_traitant NUMERIC(12,2) DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS sous_traitant_nom TEXT DEFAULT NULL"
        )
        # Refonte 3 sections (matériaux / M-O / sous-traitant) : ajustement %
        # par section + type et montant explicite pour le sous-traitant.
        # sous_traitant_montant remplace fonctionnellement cout_sous_traitant
        # dans la nouvelle UI ; on garde l'ancienne colonne pour rétro-compat.
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS ajust_materiaux NUMERIC(5,2) DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS ajust_main_oeuvre NUMERIC(5,2) DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS ajust_sous_traitant NUMERIC(5,2) DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS sous_traitant_type TEXT DEFAULT NULL, "
            "ADD COLUMN IF NOT EXISTS sous_traitant_montant NUMERIC(12,2) DEFAULT 0"
        )
        # One-shot : copier les valeurs existantes de cout_sous_traitant vers
        # sous_traitant_montant pour ne pas perdre la donnée si le déploiement
        # précédent en a saisi. WHERE évite d'écraser des valeurs déjà
        # renseignées dans la nouvelle colonne.
        cur.execute(
            "UPDATE ad_budget.budget_lignes "
            "SET sous_traitant_montant = cout_sous_traitant "
            "WHERE COALESCE(sous_traitant_montant, 0) = 0 "
            "  AND COALESCE(cout_sous_traitant, 0) <> 0"
        )
        # === Sprint A : enrichir modèle projet (statut whitelist, type_batiment,
        # region, date_adjudication, superficie_m2, dernier_snapshot_id) ===
        # 1. Migrer les statut existants vers la nouvelle whitelist AVANT le CHECK :
        # tous les projets actuels ont 'en cours' (legacy), on bascule sur 'brouillon'
        # pour rester dans la whitelist Sprint A.
        cur.execute(
            "UPDATE ad_budget.projets SET statut = 'brouillon' "
            "WHERE statut NOT IN ('brouillon', 'adjuge', 'complet', 'perdu', 'archive')"
        )
        # 2. Default + CHECK statut.
        cur.execute(
            "ALTER TABLE ad_budget.projets ALTER COLUMN statut SET DEFAULT 'brouillon'"
        )
        cur.execute(
            "ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projet_statut_check"
        )
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD CONSTRAINT projet_statut_check "
            "CHECK (statut IN ('brouillon', 'adjuge', 'complet', 'perdu', 'archive'))"
        )
        # 3. type_batiment + CHECK.
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD COLUMN IF NOT EXISTS type_batiment VARCHAR(20)"
        )
        cur.execute(
            "ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projet_type_batiment_check"
        )
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD CONSTRAINT projet_type_batiment_check "
            "CHECK (type_batiment IS NULL OR type_batiment IN ("
            "'residentiel', 'commercial', 'institutionnel', 'industriel', 'mixte'))"
        )
        # 4. region (validation côté backend, pas de CHECK SQL pour rester souple).
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD COLUMN IF NOT EXISTS region VARCHAR(50)"
        )
        # 5. date_adjudication.
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD COLUMN IF NOT EXISTS date_adjudication DATE"
        )
        # 6. superficie_m2 + CHECK > 0.
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD COLUMN IF NOT EXISTS superficie_m2 NUMERIC(10,2)"
        )
        cur.execute(
            "ALTER TABLE ad_budget.projets DROP CONSTRAINT IF EXISTS projet_superficie_m2_check"
        )
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD CONSTRAINT projet_superficie_m2_check "
            "CHECK (superficie_m2 IS NULL OR superficie_m2 > 0)"
        )
        # 7. dernier_snapshot_id (préparation Sprint B, pas utilisé maintenant).
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD COLUMN IF NOT EXISTS dernier_snapshot_id INTEGER"
        )
        # 8. Index sur statut — Ad ANA filtrera dessus.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_projet_statut ON ad_budget.projets(statut)"
        )
        # === Fix bug params globaux : persister les 4 paramètres de calcul du
        # tableau budget (mobilisation, surface_plancher, hauteur_cloisons,
        # longueur_cloisons). Avant ce fix, ces 4 champs n'existaient qu'en
        # state local React côté frontend ; perdus au refresh. NUMERIC(10,2)
        # nullable + CHECK >= 0 (les 4 valeurs peuvent légitimement être 0,
        # contrairement à superficie_m2 qui doit être > 0). ===
        for col in (
            "mobilisation", "surface_plancher",
            "hauteur_cloisons", "longueur_cloisons",
        ):
            cur.execute(
                f"ALTER TABLE ad_budget.projets "
                f"ADD COLUMN IF NOT EXISTS {col} NUMERIC(10,2)"
            )
            cur.execute(
                f"ALTER TABLE ad_budget.projets "
                f"DROP CONSTRAINT IF EXISTS projet_{col}_check"
            )
            cur.execute(
                f"ALTER TABLE ad_budget.projets "
                f"ADD CONSTRAINT projet_{col}_check "
                f"CHECK ({col} IS NULL OR {col} >= 0)"
            )
        # === Sprint B : table app_ana.project_snapshots ===
        # Snapshot du budget figé au moment où le projet bascule sur un statut
        # définitif (adjuge / complet / perdu). Consommé par Ad ANA pour les
        # analyses cross-projets (Sprint C).
        cur.execute("CREATE SCHEMA IF NOT EXISTS app_ana")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_ana.project_snapshots (
                id SERIAL PRIMARY KEY,
                projet_id INTEGER NOT NULL,
                nom_projet VARCHAR(255),
                client_nom VARCHAR(255),
                statut VARCHAR(20) NOT NULL,
                type_batiment VARCHAR(20),
                region VARCHAR(50),
                date_adjudication DATE,
                superficie_m2 NUMERIC(10,2),
                budget_lines_jsonb JSONB NOT NULL,
                aggregates_jsonb JSONB NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                trigger_event VARCHAR(50) NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                is_latest BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
        # Indexes sur les colonnes que Ad ANA filtrera. Tous IF NOT EXISTS.
        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_snapshots_projet_id "
            "ON app_ana.project_snapshots(projet_id)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_statut "
            "ON app_ana.project_snapshots(statut)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_type_batiment "
            "ON app_ana.project_snapshots(type_batiment)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_region "
            "ON app_ana.project_snapshots(region)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_client_nom "
            "ON app_ana.project_snapshots(client_nom)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_date_adjudication "
            "ON app_ana.project_snapshots(date_adjudication)",
            # Partial index sur is_latest = TRUE : permet de retrouver vite le
            # snapshot courant d'un projet sans scan complet.
            "CREATE INDEX IF NOT EXISTS idx_snapshots_is_latest "
            "ON app_ana.project_snapshots(is_latest) WHERE is_latest = TRUE",
            # GIN sur aggregates_jsonb : Ad ANA pourra filtrer par champ JSON
            # (ex: aggregates_jsonb->'totals'->>'general' > 100000).
            "CREATE INDEX IF NOT EXISTS idx_snapshots_aggregates_gin "
            "ON app_ana.project_snapshots USING GIN (aggregates_jsonb)",
        ):
            cur.execute(idx_sql)
        # FK projet.dernier_snapshot_id -> app_ana.project_snapshots(id) avec
        # ON DELETE SET NULL : si un snapshot est purgé (impossible via API
        # actuelle, mais possible en intervention manuelle), le projet ne
        # casse pas, sa FK passe à NULL.
        cur.execute(
            "ALTER TABLE ad_budget.projets "
            "DROP CONSTRAINT IF EXISTS projet_dernier_snapshot_fk"
        )
        cur.execute(
            "ALTER TABLE ad_budget.projets ADD CONSTRAINT projet_dernier_snapshot_fk "
            "FOREIGN KEY (dernier_snapshot_id) "
            "REFERENCES app_ana.project_snapshots(id) ON DELETE SET NULL"
        )
        # === Ad VIU v2 (Jalon 4) : tracabilite + idempotence du push ===
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS source_viu_analysis_id INT NULL"
        )
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS source_viu_item_id INT NULL"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_budget_lignes_viu_item "
            "ON ad_budget.budget_lignes (source_viu_item_id) "
            "WHERE source_viu_item_id IS NOT NULL"
        )
        # === Revert hors_scope feature (2026-05-12) ===
        # La feature hors_scope (deployee 2026-05-11 avec commit 3a35308)
        # a ete revertee suite a clarification metier : Ad BUD doit garder
        # son squelette complet (~180 lignes catalogue master), et un push
        # Ad VIU doit DELETER les lignes architecturales puis INSERTer les
        # items detectes. Voir nouveau comportement REPLACE dans from-viu-v2.
        #
        # DROP IF EXISTS : idempotent. Au 1er deploy post-revert, drop
        # effectif (column + index). Aux deploys suivants, no-op silencieux.
        # On peut retirer ce bloc dans 1-2 semaines une fois confirme que
        # tous les environnements ont applique le drop.
        cur.execute(
            "DROP INDEX IF EXISTS ad_budget.idx_budget_lignes_hors_scope"
        )
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "DROP COLUMN IF EXISTS hors_scope"
        )
        conn.commit()
        cur.close()
        conn.close()
        print(
            "[startup] schema ad_budget OK "
            "(source_file + type_source columns ensured)",
            flush=True,
        )
    except Exception as e:
        print(f"[startup] schema migration failed: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_schema()
    yield


app = FastAPI(title="Adision API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Whitelist explicite : Ad ADM (panneau super_admin), Ad BUD et Ad ANA
    # (les 2 frontends servis par ce backend), + dev local. allow_credentials
    # impose une whitelist nommée (le wildcard "*" est refusé par le navigateur
    # quand credentials=true).
    allow_origins=[
        "https://admin.adision.ca",
        "https://bud.adision.ca",
        "https://ana.adision.ca",
        "http://localhost:5173",
    ],
    # Couvre les preview deploys Vercel des frontends ad-bud / ad-ana.
    allow_origin_regex=r"^https://[a-z0-9-]+\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(register_ad_budget_routes(get_conn))
app.include_router(register_ad_ana_routes(get_conn))
app.include_router(register_taux_horaires_routes(get_conn))

# JWT auth deps (vérifie signature + module ad_bud, auto-provisionne dans
# ad_budget.users).
jwt_user, _jwt_user_or_token, _jwt_admin, _jwt_super_admin = make_jwt_deps(get_conn)
# _jwt_user_or_token est un alias de jwt_user (header OU ?token=) ; conservé
# dans le tuple pour compat avec d'éventuels imports externes.


@app.get("/")
def home():
    return {"message": "API Adision connectée"}


@app.get("/health")
def health():
    """Diagnostic — vérifie BD + schema ad_budget + JWT_SECRET. N'expose
    JAMAIS la valeur de JWT_SECRET, juste sa présence et sa longueur, pour
    qu'on puisse comparer entre les services Railway sans fuiter le secret.
    """
    db_ok = False
    db_error = None
    schema_ok = False
    users_table_ok = False
    users_count = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        db_ok = True
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = 'ad_budget')"
        )
        schema_ok = bool(cur.fetchone()[0])
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'ad_budget' AND table_name = 'users')"
        )
        users_table_ok = bool(cur.fetchone()[0])
        if users_table_ok:
            cur.execute("SELECT COUNT(*) FROM ad_budget.users")
            users_count = int(cur.fetchone()[0])
        cur.close()
        conn.close()
    except Exception as e:
        db_error = str(e)[:200]

    secret = os.environ.get("JWT_SECRET") or ""
    jwt_secret_configured = bool(secret)
    jwt_secret_length = len(secret) if secret else 0
    return {
        "status": "ok" if (db_ok and users_table_ok and jwt_secret_configured) else "degraded",
        "service": "ad-budget-api",
        "database": "connected" if db_ok else "unreachable",
        "database_error": db_error,
        "schema_ad_budget_exists": schema_ok,
        "users_table_exists": users_table_ok,
        "users_count": users_count,
        "jwt_secret_configured": jwt_secret_configured,
        "jwt_secret_length": jwt_secret_length,
    }


@app.get("/auth/me")
def auth_me(user=Depends(jwt_user)):
    """Renvoie le user local ad_budget.users courant (auto-provisionné si
    premier login SSO) + la liste des modules venant du JWT.

    Utilisé par le frontend Ad BUD au load de l'app pour valider le JWT et
    récupérer les infos d'affichage (nom, email, role).
    """
    return user


@app.get("/items")
def get_items():
    query = """
        SELECT *
        FROM vue_items_complets
        ORDER BY code;
    """
    df = pd.read_sql_query(query, engine)
    df = df.replace({float("nan"): None})
    df = df.fillna("")
    return df.to_dict(orient="records")


@app.get("/items/search")
def search_items(q: str = ""):
    conn = get_conn()
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT id, description
        FROM items
        WHERE actif = true
          AND description ILIKE %s
        ORDER BY description
        LIMIT 50;
    """, (f"%{q}%",))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


@app.get("/mapping/suggestions")
def get_suggestions():
    conn = get_conn()
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT 
            s.id,
            s.prix_web_item_id,
            s.item_id,
            p.nom_produit,
            p.prix,
            COALESCE(p.url_produit, '') AS url_produit,
            i.description AS item_adision,
            s.score,
            s.statut,
            s.created_at
        FROM suggestions_mapping_items s
        JOIN prix_web_items p ON p.id = s.prix_web_item_id
        JOIN items i ON i.id = s.item_id
        WHERE s.statut = 'a_valider'
        ORDER BY s.id DESC;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


@app.post("/mapping/suggestions/{suggestion_id}/set-item/{item_id}")
def set_suggestion_item(suggestion_id: int, item_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE suggestions_mapping_items
        SET item_id = %s
        WHERE id = %s;
    """, (item_id, suggestion_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "updated"}


@app.post("/mapping/suggestions/{suggestion_id}/accept")
def accept_suggestion(suggestion_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT prix_web_item_id, item_id
        FROM suggestions_mapping_items
        WHERE id = %s;
    """, (suggestion_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": "Suggestion introuvable"}
    prix_web_item_id, item_id = row
    cur.execute("""
        UPDATE suggestions_mapping_items
        SET statut = 'accepte'
        WHERE id = %s;
    """, (suggestion_id,))
    cur.execute("""
        UPDATE prix_web_items
        SET item_id = %s, statut = 'valide'
        WHERE id = %s;
    """, (item_id, prix_web_item_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "accepted"}


@app.post("/mapping/suggestions/{suggestion_id}/accept-learn")
def accept_and_learn(suggestion_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.prix_web_item_id, s.item_id, p.nom_produit
        FROM suggestions_mapping_items s
        JOIN prix_web_items p ON p.id = s.prix_web_item_id
        WHERE s.id = %s;
    """, (suggestion_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": "Suggestion introuvable"}
    prix_web_item_id, item_id, nom_produit = row
    cur.execute("""
        UPDATE suggestions_mapping_items SET statut = 'accepte' WHERE id = %s;
    """, (suggestion_id,))
    cur.execute("""
        UPDATE prix_web_items SET item_id = %s, statut = 'valide' WHERE id = %s;
    """, (item_id, prix_web_item_id))
    cur.execute("""
        INSERT INTO apprentissage_mapping_items (nom_produit_web, item_id, source)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING;
    """, (nom_produit, item_id, "validation_ui"))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "accepted_and_learned"}


@app.post("/mapping/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE suggestions_mapping_items SET statut = 'rejete' WHERE id = %s;
    """, (suggestion_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "rejected"}