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

    return router
