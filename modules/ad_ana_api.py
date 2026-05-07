"""Routes Ad ANA — analyses cross-projets sur app_ana.project_snapshots.

Sprint C : module séparé du budget pour découpler la logique d'agrégation
cross-projets de la logique de saisie. Toutes les routes /ana/* requièrent
un JWT valide (via dependencies router-level).

Pour Sprint C v1, tous les users authentifiés voient tous les snapshots.
Pas de scope multi-tenant — à ajouter dans un sprint futur si besoin.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg2.extras import RealDictCursor

from modules.auth_jwt import make_jwt_deps


# Limite hard du payload renvoyé par GET /snapshots — protège la BD et le
# bundle réseau d'un user qui demanderait limit=999999.
SNAPSHOTS_MAX_LIMIT = 1000
SNAPSHOTS_DEFAULT_LIMIT = 200

# Colonnes retournées par GET /snapshots. budget_lines_jsonb est OMIS — pour
# les analyses cross-projets, aggregates_jsonb suffit. L'inclure multiplierait
# la taille du payload par ~10 sans valeur ajoutée.
_SNAPSHOT_LIST_COLS = (
    "id, projet_id, nom_projet, client_nom, statut, type_batiment, "
    "region, date_adjudication, superficie_m2, aggregates_jsonb, "
    "created_at, trigger_event, schema_version, is_latest"
)


def _parse_csv(value: Optional[str]) -> list:
    """Parse 'a,b,c' -> ['a','b','c']. Filtre les valeurs vides. Renvoie []
    si value est None ou vide."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def register_ad_ana_routes(get_conn):
    jwt_user, _, _ = make_jwt_deps(get_conn)

    # Toutes les routes /ana/* exigent un JWT valide. Pas de check de module
    # spécifique (ad_ana) en Sprint C v1 — n'importe quel user authentifié
    # peut accéder. À durcir au Sprint C+1 si besoin.
    router = APIRouter(
        prefix="/ana",
        tags=["Ad ANA"],
        dependencies=[Depends(jwt_user)],
    )

    @router.get("/snapshots")
    def list_snapshots(
        statut: Optional[str] = None,
        type_batiment: Optional[str] = None,
        region: Optional[str] = None,
        client_nom: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        is_latest_only: bool = True,
        limit: int = SNAPSHOTS_DEFAULT_LIMIT,
        offset: int = 0,
    ):
        """Liste paginée des snapshots avec filtres. Format de réponse :
            { "total": int, "items": [snapshot, ...] }

        Filtres multi-valeurs (statut, type_batiment, region) : passés en
        liste séparée par des virgules (ex: ?statut=adjuge,complet).
        """
        # Clamp limit/offset (sécurité + DX : un client qui passe limit=99999
        # reçoit 1000 sans erreur)
        limit = max(1, min(int(limit), SNAPSHOTS_MAX_LIMIT))
        offset = max(0, int(offset))

        statuts = _parse_csv(statut)
        types = _parse_csv(type_batiment)
        regions = _parse_csv(region)

        where = []
        params = []
        if is_latest_only:
            where.append("is_latest = TRUE")
        if statuts:
            where.append("statut = ANY(%s)")
            params.append(statuts)
        if types:
            where.append("type_batiment = ANY(%s)")
            params.append(types)
        if regions:
            where.append("region = ANY(%s)")
            params.append(regions)
        if client_nom:
            where.append("client_nom ILIKE %s")
            params.append(f"%{client_nom}%")
        if date_from:
            where.append("date_adjudication >= %s")
            params.append(date_from)
        if date_to:
            where.append("date_adjudication <= %s")
            params.append(date_to)

        where_sql = " AND ".join(where) if where else "TRUE"

        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # COUNT séparé : OK pour Sprint C v1, optimisable au besoin avec
            # une window function COUNT(*) OVER ().
            cur.execute(
                f"SELECT COUNT(*) AS n FROM app_ana.project_snapshots "
                f"WHERE {where_sql}",
                params,
            )
            total = cur.fetchone()["n"]

            cur.execute(
                f"SELECT {_SNAPSHOT_LIST_COLS} "
                f"FROM app_ana.project_snapshots "
                f"WHERE {where_sql} "
                f"ORDER BY created_at DESC "
                f"LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            items = cur.fetchall()
        finally:
            cur.close()
            conn.close()

        return {
            "total": total,
            "items": items,
            "limit": limit,
            "offset": offset,
        }

    @router.get("/comparator/{projet_id}")
    def comparator(projet_id: int, top_n: int = 5):
        """Trouve les snapshots les plus similaires au snapshot is_latest du
        projet de référence. Score de similarité (max 95) :
          - Type bâtiment match exact (et non NULL) : +50
          - Région match exact (et non NULL) : +20
          - Superficie ±20% : +20  (sinon ±50% : +10)
          - Client_nom match exact (et non NULL) : +5

        Renvoie la référence + top_n candidats (clamp 1-20) avec score > 0.
        """
        top_n = max(1, min(int(top_n), 20))
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # 1. Snapshot de référence (is_latest du projet demandé)
            cur.execute(
                f"SELECT {_SNAPSHOT_LIST_COLS} "
                f"FROM app_ana.project_snapshots "
                f"WHERE projet_id = %s AND is_latest = TRUE LIMIT 1",
                (projet_id,),
            )
            ref = cur.fetchone()
            if not ref:
                raise HTTPException(
                    status_code=404,
                    detail="Aucun snapshot pour ce projet (le projet doit "
                    "etre passe en statut adjuge/complet/perdu).",
                )

            # 2. Tous les autres snapshots is_latest
            cur.execute(
                f"SELECT {_SNAPSHOT_LIST_COLS} "
                f"FROM app_ana.project_snapshots "
                f"WHERE is_latest = TRUE AND projet_id <> %s",
                (projet_id,),
            )
            candidates = cur.fetchall()
        finally:
            cur.close()
            conn.close()

        # 3. Scorer chaque candidat
        ref_type = ref.get("type_batiment")
        ref_region = ref.get("region")
        ref_surf = ref.get("superficie_m2")
        ref_client = ref.get("client_nom")
        ref_surf_f = float(ref_surf) if ref_surf is not None else None

        scored = []
        for c in candidates:
            score = 0
            if c["type_batiment"] and c["type_batiment"] == ref_type:
                score += 50
            if c["region"] and c["region"] == ref_region:
                score += 20
            c_surf = c.get("superficie_m2")
            if ref_surf_f and c_surf is not None:
                ratio = float(c_surf) / ref_surf_f
                if 0.8 <= ratio <= 1.2:
                    score += 20
                elif 0.5 <= ratio <= 2.0:
                    score += 10
            if c["client_nom"] and c["client_nom"] == ref_client:
                score += 5
            if score > 0:
                # Mutate the dict to add score (RealDictRow accepte ça)
                c_with_score = dict(c)
                c_with_score["score"] = score
                scored.append(c_with_score)

        scored.sort(key=lambda x: -x["score"])
        return {
            "reference": dict(ref),
            "similar": scored[:top_n],
            "candidates_evaluated": len(candidates),
        }

    return router
