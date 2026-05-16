"""Endpoints /admin/taux-horaires/* — administration de la grille des taux
horaires CCQ (Ad BUD).

Source de données : tables ad_budget.taux_horaires (référence des taux par
métier/occupation/non-syndiqué) et ad_budget.csi_division_default_metier
(mapping division CSI MasterFormat -> taux par défaut). Cf. migrations
sprint2_taux_horaires_{schema,seed}.sql.

Toutes les routes sont réservées au rôle super_admin (dependency posée au
niveau du router). Le module expose aussi `_resolve_taux_default`, helper
réutilisé par la logique d'application du taux par défaut à la création
d'une budget_lignes (phase D5).

Pattern calqué sur ad_budget_api.py / ad_ana_api.py : factory
register_*_routes(get_conn), get_conn() + cursor(row_factory=dict_row),
try/finally close, clamp limit/offset, erreurs via HTTPException.
"""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from modules.auth_jwt import make_jwt_deps

VALID_GROUPES = {"metier", "occupation", "non_syndique"}

# Colonnes retournées pour une ligne de taux_horaires (ordre stable).
_TAUX_COLS = (
    "id, code, metier, qualification, taux_col17, groupe, ordre, "
    "notes, actif, updated_at, updated_by"
)


# ─────────────────────────────────────────────────────────────────────────
# Modèles Pydantic — bodies POST / PATCH
# ─────────────────────────────────────────────────────────────────────────

class TauxHoraireCreate(BaseModel):
    code: str = Field(..., min_length=1)
    metier: str = Field(..., min_length=1)
    qualification: Optional[str] = None
    taux_col17: Decimal = Field(..., gt=0)
    groupe: str
    ordre: int = 0
    notes: Optional[str] = None


class TauxHorairePatch(BaseModel):
    code: Optional[str] = Field(None, min_length=1)
    metier: Optional[str] = Field(None, min_length=1)
    qualification: Optional[str] = None
    taux_col17: Optional[Decimal] = None
    groupe: Optional[str] = None
    ordre: Optional[int] = None
    notes: Optional[str] = None
    actif: Optional[bool] = None


class CsiMappingPatch(BaseModel):
    taux_id: int


# ─────────────────────────────────────────────────────────────────────────
# Helper — résolution du taux par défaut depuis une section CSI
# ─────────────────────────────────────────────────────────────────────────

def _resolve_taux_default(csi_section: Optional[str], conn) -> Optional[Decimal]:
    """Résout le taux horaire par défaut d'une section MasterFormat via sa
    division (2 premiers caractères).

    Args:
        csi_section: ex "06 40 0", "0640", " 06", ou None.
        conn: connexion psycopg active. Passée par l'appelant — permet
            l'usage dans une transaction existante (cf. phase D5, auto-fill
            au push Ad VIU) sans ouvrir une 2e connexion.

    Returns:
        Le taux_col17 (Decimal) du métier mappé à la division, ou None si :
        section vide/None, < 2 caractères, division non mappée, ou taux
        résolu marqué actif=false.
    """
    if not csi_section:
        return None
    division = csi_section.strip()[:2]
    if len(division) < 2:
        return None
    cur = conn.cursor(row_factory=dict_row)
    try:
        cur.execute(
            """
            SELECT t.taux_col17
            FROM ad_budget.csi_division_default_metier m
            JOIN ad_budget.taux_horaires t ON t.id = m.taux_id
            WHERE m.csi_division = %s AND t.actif = TRUE
            """,
            (division,),
        )
        row = cur.fetchone()
    finally:
        cur.close()
    return row["taux_col17"] if row else None


# ─────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────

def register_taux_horaires_routes(get_conn):
    # make_jwt_deps retourne (jwt_user, jwt_user_or_token, jwt_admin, jwt_super_admin)
    jwt_user, _, _, jwt_super_admin = make_jwt_deps(get_conn)

    # Router parent neutre — aucun prefix, aucune dependency propre. Il agrège
    # deux sous-routers aux contrôles d'accès distincts (include_router en fin
    # de fonction). api.py monte ce parent tel quel : la refonte est invisible
    # côté appelant.
    parent = APIRouter()

    # Sous-router ADMIN — les 7 endpoints d'administration, réservés super_admin
    # (dependency posée au niveau du sous-router).
    admin = APIRouter(
        prefix="/admin",
        tags=["Taux horaires"],
        dependencies=[Depends(jwt_super_admin)],
    )

    # ── GET /admin/taux-horaires — liste paginée + filtres ───────────────
    @admin.get("/taux-horaires")
    def list_taux_horaires(
        groupe: Optional[str] = None,
        actif: str = "true",
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ):
        """Liste paginée. Réponse : { total, items[], limit, offset }.
        `actif` : 'true' (défaut) | 'false' | 'all'. Tri groupe, ordre, metier.
        """
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        where = []
        params: list = []
        if groupe:
            where.append("groupe = %s")
            params.append(groupe)
        if actif == "true":
            where.append("actif = TRUE")
        elif actif == "false":
            where.append("actif = FALSE")
        # actif == "all" (ou autre) -> pas de filtre actif
        if search:
            where.append("(metier ILIKE %s OR code ILIKE %s)")
            like = f"%{search}%"
            params.extend([like, like])
        where_sql = " AND ".join(where) if where else "TRUE"

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                f"SELECT COUNT(*) AS n FROM ad_budget.taux_horaires WHERE {where_sql}",
                params,
            )
            total = cur.fetchone()["n"]
            cur.execute(
                f"SELECT {_TAUX_COLS} FROM ad_budget.taux_horaires "
                f"WHERE {where_sql} "
                f"ORDER BY groupe, ordre, metier "
                f"LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            items = cur.fetchall()
        finally:
            cur.close()
            conn.close()
        return {"total": total, "items": items, "limit": limit, "offset": offset}

    # ── GET /admin/taux-horaires/{id} — détail ───────────────────────────
    @admin.get("/taux-horaires/{taux_id}")
    def get_taux_horaire(taux_id: int):
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                f"SELECT {_TAUX_COLS} FROM ad_budget.taux_horaires WHERE id = %s",
                (taux_id,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Taux horaire introuvable")
        return row

    # ── POST /admin/taux-horaires — création ─────────────────────────────
    @admin.post("/taux-horaires", status_code=201)
    def create_taux_horaire(
        body: TauxHoraireCreate,
        payload: dict = Depends(jwt_super_admin),
    ):
        if body.groupe not in VALID_GROUPES:
            raise HTTPException(
                status_code=400,
                detail=f"groupe invalide : '{body.groupe}' "
                       f"(attendu : {', '.join(sorted(VALID_GROUPES))})",
            )
        if body.taux_col17 <= 0:
            raise HTTPException(status_code=400, detail="taux_col17 doit être > 0")
        updated_by = payload.get("email") or payload.get("sub") or "unknown"

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT 1 FROM ad_budget.taux_horaires WHERE code = %s",
                (body.code,),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail=f"Le code '{body.code}' existe déjà",
                )
            cur.execute(
                f"""
                INSERT INTO ad_budget.taux_horaires
                    (code, metier, qualification, taux_col17, groupe, ordre,
                     notes, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_TAUX_COLS}
                """,
                (body.code, body.metier, body.qualification, body.taux_col17,
                 body.groupe, body.ordre, body.notes, updated_by),
            )
            row = cur.fetchone()
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return row

    # ── PATCH /admin/taux-horaires/{id} — modification partielle ─────────
    @admin.patch("/taux-horaires/{taux_id}")
    def update_taux_horaire(
        taux_id: int,
        body: TauxHorairePatch,
        payload: dict = Depends(jwt_super_admin),
    ):
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            raise HTTPException(status_code=400, detail="Aucun champ à modifier")
        if "groupe" in fields and fields["groupe"] not in VALID_GROUPES:
            raise HTTPException(
                status_code=400,
                detail=f"groupe invalide : '{fields['groupe']}' "
                       f"(attendu : {', '.join(sorted(VALID_GROUPES))})",
            )
        if "taux_col17" in fields and fields["taux_col17"] is not None \
                and fields["taux_col17"] <= 0:
            raise HTTPException(status_code=400, detail="taux_col17 doit être > 0")
        updated_by = payload.get("email") or payload.get("sub") or "unknown"

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT id FROM ad_budget.taux_horaires WHERE id = %s",
                (taux_id,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Taux horaire introuvable")

            if "code" in fields:
                cur.execute(
                    "SELECT 1 FROM ad_budget.taux_horaires "
                    "WHERE code = %s AND id <> %s",
                    (fields["code"], taux_id),
                )
                if cur.fetchone():
                    raise HTTPException(
                        status_code=409,
                        detail=f"Le code '{fields['code']}' existe déjà",
                    )

            # updated_at est géré par le trigger BD set_updated_at.
            set_parts = [f"{k} = %s" for k in fields]
            params = list(fields.values())
            set_parts.append("updated_by = %s")
            params.append(updated_by)
            params.append(taux_id)
            cur.execute(
                f"UPDATE ad_budget.taux_horaires SET {', '.join(set_parts)} "
                f"WHERE id = %s RETURNING {_TAUX_COLS}",
                params,
            )
            row = cur.fetchone()
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return row

    # ── DELETE /admin/taux-horaires/{id} — soft-delete (actif=false) ─────
    @admin.delete("/taux-horaires/{taux_id}")
    def soft_delete_taux_horaire(
        taux_id: int,
        payload: dict = Depends(jwt_super_admin),
    ):
        """Soft-delete : passe actif=false. Bloqué (409) si le taux est encore
        référencé comme défaut d'une ou plusieurs divisions CSI — l'erreur
        liste les divisions à réassigner d'abord."""
        updated_by = payload.get("email") or payload.get("sub") or "unknown"

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT id FROM ad_budget.taux_horaires WHERE id = %s",
                (taux_id,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Taux horaire introuvable")

            cur.execute(
                "SELECT csi_division FROM ad_budget.csi_division_default_metier "
                "WHERE taux_id = %s ORDER BY csi_division",
                (taux_id,),
            )
            divisions = [r["csi_division"] for r in cur.fetchall()]
            if divisions:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Taux référencé comme défaut des divisions CSI "
                        f"{', '.join(divisions)}. Réassignez ces divisions à un "
                        f"autre taux avant de désactiver celui-ci."
                    ),
                )

            cur.execute(
                f"UPDATE ad_budget.taux_horaires "
                f"SET actif = FALSE, updated_by = %s "
                f"WHERE id = %s RETURNING {_TAUX_COLS}",
                (updated_by, taux_id),
            )
            row = cur.fetchone()
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return row

    # ── GET /admin/csi-division-mapping — liste des 19 mappings ──────────
    @admin.get("/csi-division-mapping")
    def list_csi_division_mapping():
        """Liste les mappings division CSI -> taux par défaut, avec les infos
        du métier joint. Réponse : { items[] }."""
        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                """
                SELECT m.csi_division, m.taux_id, m.updated_at, m.updated_by,
                       t.code AS taux_code, t.metier AS taux_metier,
                       t.taux_col17, t.actif AS taux_actif
                FROM ad_budget.csi_division_default_metier m
                JOIN ad_budget.taux_horaires t ON t.id = m.taux_id
                ORDER BY m.csi_division
                """
            )
            items = cur.fetchall()
        finally:
            cur.close()
            conn.close()
        return {"items": items}

    # ── PATCH /admin/csi-division-mapping/{csi_division} ─────────────────
    @admin.patch("/csi-division-mapping/{csi_division}")
    def update_csi_division_mapping(
        csi_division: str,
        body: CsiMappingPatch,
        payload: dict = Depends(jwt_super_admin),
    ):
        """Réassigne la division CSI à un autre taux. Le taux cible doit
        exister et être actif."""
        updated_by = payload.get("email") or payload.get("sub") or "unknown"

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                "SELECT csi_division FROM ad_budget.csi_division_default_metier "
                "WHERE csi_division = %s",
                (csi_division,),
            )
            if not cur.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail=f"Division CSI '{csi_division}' absente du mapping",
                )

            cur.execute(
                "SELECT actif FROM ad_budget.taux_horaires WHERE id = %s",
                (body.taux_id,),
            )
            target = cur.fetchone()
            if not target:
                raise HTTPException(
                    status_code=400,
                    detail=f"taux_id {body.taux_id} inexistant",
                )
            if not target["actif"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"taux_id {body.taux_id} est inactif — "
                           f"choisir un taux actif",
                )

            cur.execute(
                "UPDATE ad_budget.csi_division_default_metier "
                "SET taux_id = %s, updated_by = %s "
                "WHERE csi_division = %s "
                "RETURNING csi_division, taux_id, updated_at, updated_by",
                (body.taux_id, updated_by, csi_division),
            )
            row = cur.fetchone()
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return row

    # Sous-router USER — lecture seule de la grille des taux, ouverte à tout
    # utilisateur Ad BUD authentifié (alimente le TauxHorairePicker côté
    # frontend, phase D4). Aucun accès aux mutations ni au mapping CSI.
    user = APIRouter(
        prefix="/budget",
        tags=["Taux horaires"],
        dependencies=[Depends(jwt_user)],
    )

    # ── GET /budget/taux-horaires — liste des taux actifs (lecture user) ──
    @user.get("/taux-horaires")
    def list_taux_horaires_public(
        groupe: Optional[str] = None,
        search: Optional[str] = None,
    ):
        """Liste des taux horaires actifs — alimente le picker côté Ad BUD.

        Réponse : { items[] }. N'expose que les 6 champs utiles au picker
        (id, code, metier, qualification, taux_col17, groupe) — pas de
        notes / ordre / actif / updated_*. Pas de pagination (jeu réduit,
        ~175 lignes). Filtres groupe / search optionnels.
        """
        where = ["actif = TRUE"]
        params: list = []
        if groupe:
            where.append("groupe = %s")
            params.append(groupe)
        if search:
            where.append("(metier ILIKE %s OR code ILIKE %s)")
            like = f"%{search}%"
            params.extend([like, like])
        where_sql = " AND ".join(where)

        conn = get_conn()
        cur = conn.cursor(row_factory=dict_row)
        try:
            cur.execute(
                f"SELECT id, code, metier, qualification, taux_col17, groupe "
                f"FROM ad_budget.taux_horaires WHERE {where_sql} "
                f"ORDER BY groupe, ordre, metier",
                params,
            )
            items = cur.fetchall()
        finally:
            cur.close()
            conn.close()
        return {"items": items}

    parent.include_router(admin)
    parent.include_router(user)
    return parent
