from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from modules.ad_ana_api import register_ad_ana_routes
from modules.ad_budget_api import register_ad_budget_routes
from modules.ad_gabarits_api import register_ad_gabarits_routes
from modules.ad_export_gabarits_api import register_ad_export_gabarits_routes
from modules.ad_devis_api import register_ad_devis_routes
from modules.ad_budget_matlink_api import register_ad_budget_matlink_routes
from modules.taux_horaires_api import register_taux_horaires_routes
from modules.auth_jwt import make_jwt_deps
import os
import secrets

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
        # Passerelle Ad RES → Ad BUD : lien LOGIQUE (cross-DB, pas de FK
        # physique) vers app_central.contacts.id (UUID, base adision-app-api).
        # NULL = saisie libre ou ligne ancienne (rétrocompat). Le nom texte
        # reste dans sous_traitant_nom pour l'historique même si le contact
        # change/disparaît.
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS sous_traitant_contact_id UUID DEFAULT NULL"
        )
        # Pont Ad TYP → Ad BUD : lien LOGIQUE (cross-DB, pas de FK) vers la ligne
        # Ad TYP d'origine + horodatage du snapshot. NULL = ligne manuelle
        # classique. La re-tarif depuis Ad TYP n'est permise que si le projet est
        # au statut 'brouillon' (« En cours ») — gelée sinon.
        cur.execute(
            "ALTER TABLE ad_budget.budget_lignes "
            "ADD COLUMN IF NOT EXISTS source_typ_code TEXT DEFAULT NULL, "
            "ADD COLUMN IF NOT EXISTS source_typ_snapshot_at TIMESTAMPTZ DEFAULT NULL"
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
        # 3-6. Phase 7A — colonnes d'IDENTITÉ (type_batiment / region /
        # date_adjudication / superficie_m2) NE SONT PLUS recréées au boot :
        # l'identité projet vit dans Ad HUB (source unique). Les ADD COLUMN IF NOT
        # EXISTS + leurs CHECK ont été RETIRÉS ici pour que le DROP de Phase 7B ne
        # soit pas annulé au prochain démarrage (les CHECK partent avec le DROP
        # COLUMN ; le re-pointage des lectures est fait en Phase 7A).
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


# Migrations migrations/sprint_*.sql appliquées AVANT l'existence de
# _bootstrap_db (à la main par le passé). Pré-enregistrées « jouées » ->
# jamais ré-exécutées. sprint2_taux_horaires_seed.sql est un SEED : un
# rejouage dupliquerait les données — d'où ce garde-fou.
_LEGACY_MIGRATIONS = (
    "sprint1_5_ad_mat_links.sql",
    "sprint2_taux_horaires_schema.sql",
    "sprint2_taux_horaires_seed.sql",
    "sprint_a_ad_bud_metadata.sql",
)


def _bootstrap_db():
    """Applique au démarrage les migrations migrations/*.sql non encore jouées.

    Suivi : table ad_budget.schema_migrations (une ligne par fichier joué) —
    un fichier déjà enregistré est ignoré ; chaque migration ne tourne qu'UNE
    fois, quelle que soit l'idempotence de son SQL. Les fichiers legacy
    (_LEGACY_MIGRATIONS) sont pré-enregistrés : appliqués avant ce mécanisme,
    on ne les rejoue jamais.

    Best-effort : toute erreur est logguée, l'app démarre quand même (même
    esprit que _ensure_schema). Migration + enregistrement = une transaction ;
    une migration en échec n'est pas enregistrée -> réessayée au prochain boot.
    """
    try:
        conn = get_conn()
    except Exception as e:
        print(f"[bootstrap] connexion BD échouée : {e}", flush=True)
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ad_budget.schema_migrations ("
            " filename TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        for legacy in _LEGACY_MIGRATIONS:
            cur.execute(
                "INSERT INTO ad_budget.schema_migrations (filename) VALUES (%s)"
                " ON CONFLICT (filename) DO NOTHING",
                (legacy,),
            )
        conn.commit()
        cur.execute("SELECT filename FROM ad_budget.schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        cur.close()

        migrations_dir = Path(__file__).parent / "migrations"
        if not migrations_dir.is_dir():
            print("[bootstrap] dossier migrations/ absent", flush=True)
            return
        files = sorted(migrations_dir.glob("*.sql"))
        print(
            f"[bootstrap] {len(files)} fichier(s) SQL ; déjà joués : "
            f"{sorted(done)}",
            flush=True,
        )
        for f in files:
            if f.name in done:
                continue
            sql = f.read_text(encoding="utf-8")
            cur = conn.cursor()
            try:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO ad_budget.schema_migrations (filename)"
                    " VALUES (%s) ON CONFLICT (filename) DO NOTHING",
                    (f.name,),
                )
                conn.commit()
                print(f"[bootstrap] {f.name} ✓", flush=True)
            except Exception as e:
                conn.rollback()
                print(
                    f"[bootstrap] ERREUR sur {f.name} : "
                    f"{type(e).__name__} : {e}",
                    flush=True,
                )
            finally:
                cur.close()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_schema()
    _bootstrap_db()
    yield


app = FastAPI(title="Adision API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Whitelist explicite : Ad ADM (super_admin), Ad HUB, et tous les
    # satellites Ad FLO qui consomment des endpoints servis par ce backend.
    # Sprint 6 (28 mai 2026) — Ad EST ajouté : nouveau consommateur de
    # /budget/taux-horaires via @adision/ui TauxHorairePicker mode
    # four_levels. Tant qu'à faire, mat/con/est listés pour éviter de
    # rejouer ce ticket à chaque module nouveau qui consommera la grille
    # ACQ (single source of truth ad_budget.taux_horaires).
    # allow_credentials=True impose une whitelist nommée (wildcard refusé
    # par le navigateur quand credentials=true).
    allow_origins=[
        "https://app.adision.ca",     # Ad HUB (adision-app)
        "https://admin.adision.ca",   # Ad ADM
        "https://bud.adision.ca",     # Ad BUD
        "https://viu.adision.ca",     # Ad VIU (push budget ← v2)
        "https://ana.adision.ca",     # Ad ANA
        "https://est.adision.ca",     # Ad EST (Sprint 6 — picker ACQ 4 niveaux)
        "https://mat.adision.ca",     # Ad MAT (futur consommateur potentiel)
        "https://con.adision.ca",     # Ad CON (futur consommateur potentiel)
        "https://typ.adision.ca",     # Ad TYP (éditeur d'assemblage — taux horaires ACQ)
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
    ],
    # Couvre les preview deploys Vercel de tous les frontends Ad FLO.
    allow_origin_regex=r"^https://[a-z0-9-]+\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(register_ad_budget_routes(get_conn))
app.include_router(register_ad_gabarits_routes(get_conn))
app.include_router(register_ad_export_gabarits_routes(get_conn))
app.include_router(register_ad_devis_routes(get_conn))
app.include_router(register_ad_budget_matlink_routes(get_conn))
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


@app.post("/internal/backfill-organization")
def internal_backfill_organization(
    data: dict,
    x_internal_secret: str = Header(None),
):
    """Endpoint service-à-service (PHASE 4A) : propage organization_id aux
    projets d'un user, identifié par email. Appelé par adision-app-api au
    rattachement d'un user à une organisation.

    Auth : header X-Internal-Secret == INTERNAL_SERVICE_SECRET (secret partagé
    entre les 3 backends). PAS de JWT — appel machine-à-machine.

    Body {email, organization_id} : résout email -> id local ad_budget.users
    puis UPDATE ad_budget.projets. Email inconnu localement -> no-op silencieux
    (le user ne s'est jamais connecté à Ad BUD). Retourne {updated: <nb lignes>}.
    """
    expected = os.environ.get("INTERNAL_SERVICE_SECRET")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="INTERNAL_SERVICE_SECRET non configuré côté serveur",
        )
    # compare_digest : comparaison à temps constant. Le `not x_internal_secret`
    # en court-circuit garantit qu'on n'appelle compare_digest qu'avec deux str
    # (header absent/vide -> 401 sans l'appeler).
    if not x_internal_secret or not secrets.compare_digest(x_internal_secret, expected):
        raise HTTPException(status_code=401, detail="Secret de service invalide")

    email = (data.get("email") or "").strip().lower()
    organization_id = data.get("organization_id")
    if not email or not organization_id:
        raise HTTPException(
            status_code=400,
            detail="Champs 'email' et 'organization_id' requis",
        )

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM ad_budget.users WHERE LOWER(email) = LOWER(%s)",
            (email,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return {"updated": 0}
        cur.execute(
            "UPDATE ad_budget.projets SET organization_id = %s WHERE user_id = %s",
            (organization_id, row[0]),
        )
        updated = cur.rowcount
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return {"updated": updated}


@app.patch("/internal/budget-verrou-mirror/{hub_id}")
def internal_sync_verrou_mirror(
    hub_id: int,
    data: dict,
    x_internal_secret: str = Header(None),
):
    """Endpoint service-à-service (juin 2026) : alignement HUB → BUD du miroir
    local du verrou projet. Appelé par adision-app-api après son toggle de
    app_hub.projects.is_verrouille pour propager IMMÉDIATEMENT vers le miroir
    BUD, sans attendre un pull au prochain GET projet. Élimine le stale (cf.
    incident verrou 309/310).

    Auth : X-Internal-Secret == INTERNAL_SERVICE_SECRET (secret partagé). PAS
    de JWT — appel machine-à-machine. Volontairement HORS du router /budget
    (qui exige module ad_bud dans le JWT), car un super_admin sans module
    ad_bud doit pouvoir toggler le verrou et déclencher la propagation.

    Body : {is_verrouille: bool, verrouille_par_email: str|null,
            verrouille_le: iso8601|null}.
    Scope : UPDATE TOUS les budgets non-archive liés au hub_id (1 actif + N
    archives). Idempotent."""
    expected = os.environ.get("INTERNAL_SERVICE_SECRET")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="INTERNAL_SERVICE_SECRET non configuré côté serveur",
        )
    if not x_internal_secret or not secrets.compare_digest(x_internal_secret, expected):
        raise HTTPException(status_code=401, detail="Secret de service invalide")

    is_locked = bool((data or {}).get("is_verrouille"))
    par_email = (data or {}).get("verrouille_par_email") or "(via hub)"
    verrouille_le = (data or {}).get("verrouille_le")

    conn = get_conn()
    cur = conn.cursor()
    try:
        if is_locked:
            cur.execute(
                "UPDATE ad_budget.projets SET is_verrouille = TRUE, "
                "    verrouille_par = %s, "
                "    verrouille_le = COALESCE(%s::timestamptz, NOW()), "
                "    updated_at = NOW() "
                "WHERE ad_hub_project_id = %s AND statut <> 'archive' "
                "RETURNING id",
                (par_email, verrouille_le, int(hub_id)),
            )
        else:
            cur.execute(
                "UPDATE ad_budget.projets SET is_verrouille = FALSE, "
                "    verrouille_par = NULL, verrouille_le = NULL, updated_at = NOW() "
                "WHERE ad_hub_project_id = %s AND statut <> 'archive' "
                "RETURNING id",
                (int(hub_id),),
            )
        rows = cur.fetchall()
        updated_ids = [r[0] for r in rows]
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return {"status": "ok", "hub_id": int(hub_id), "is_verrouille": is_locked,
            "updated_ids": updated_ids, "rowcount": len(updated_ids)}