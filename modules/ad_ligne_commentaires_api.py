"""Commentaires sur une ligne de budget (18 septembre 2026).

Simon : « quand je clic droit sur ma souris j'aimerais voir une modale avec
des choix options. Option 1 : ajouter un commentaire à la description de
l'item. Dans la cellule de l'item à droite, une icône C en rouge apparaît.
Quand on clique dessus, une fenêtre avec les commentaires apparaît. On peut
ajouter des commentaires, modifier, supprimer, date et nom de l'éditeur. »

Un fil par ligne (table ad_budget.ligne_commentaires, migration
sprint_ligne_commentaires.sql), distinct de la colonne `note` : chaque entrée
porte son auteur et ses dates.

Accès : quiconque peut LIRE le projet peut le commenter (mode 'read' de
_load_and_authorize_projet). Un commentaire n'altère pas le budget ; le
verrou (gel du chiffrage) et la détention (un seul éditeur à la fois) n'ont
donc pas à empêcher une revue. Modifier ou supprimer un commentaire reste
réservé à son AUTEUR.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from modules.auth_jwt import make_jwt_deps
from modules.ad_budget_api import _load_and_authorize_projet

_COLONNES = "id, ligne_id, auteur_id, auteur_nom, corps, created_at, updated_at"


class CommentaireSaisie(BaseModel):
    corps: str = Field(..., min_length=1, max_length=5000)


def _auteur(user: dict) -> tuple[Optional[int], str]:
    try:
        uid = int(user.get("id"))
    except (TypeError, ValueError):
        uid = None
    nom = (user.get("nom") or user.get("email") or "Utilisateur").strip()
    return uid, nom


def register_ad_ligne_commentaires_routes(get_conn):
    jwt_user, _jwt_user_or_token, _jwt_admin, _ = make_jwt_deps(get_conn)
    router = APIRouter(prefix="/budget", tags=["Ad Budget — commentaires"],
                       dependencies=[Depends(jwt_user)])

    def _ligne_du_projet(cur, projet_id: int, ligne_id: int):
        cur.execute(
            "SELECT 1 FROM ad_budget.budget_lignes WHERE id = %s AND projet_id = %s",
            (ligne_id, projet_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Ligne introuvable dans ce projet")

    def _commentaire_a_soi(cur, projet_id: int, commentaire_id: int, user: dict) -> dict:
        cur.execute(
            f"SELECT {_COLONNES} FROM ad_budget.ligne_commentaires "
            "WHERE id = %s AND projet_id = %s",
            (commentaire_id, projet_id),
        )
        c = cur.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="Commentaire introuvable")
        uid, _nom = _auteur(user)
        if uid is None or c["auteur_id"] != uid:
            raise HTTPException(status_code=403,
                                detail="Seul l'auteur peut modifier ou supprimer ce commentaire")
        return c

    # Nombre de commentaires par ligne : de quoi poser le « C » rouge sur tout
    # le tableau en une seule lecture.
    @router.get("/projets/{projet_id}/commentaires/compte")
    def compter(projet_id: int, user=Depends(jwt_user)):
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT ligne_id, COUNT(*) FROM ad_budget.ligne_commentaires "
                "WHERE projet_id = %s GROUP BY ligne_id",
                (projet_id,),
            )
            return {"compte": {str(r[0]): int(r[1]) for r in cur.fetchall()}}
        finally:
            conn.close()

    @router.get("/projets/{projet_id}/lignes/{ligne_id}/commentaires")
    def lister(projet_id: int, ligne_id: int, user=Depends(jwt_user)):
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            _ligne_du_projet(cur, projet_id, ligne_id)
            cur.execute(
                f"SELECT {_COLONNES} FROM ad_budget.ligne_commentaires "
                "WHERE ligne_id = %s AND projet_id = %s ORDER BY created_at, id",
                (ligne_id, projet_id),
            )
            return {"commentaires": cur.fetchall()}
        finally:
            conn.close()

    @router.post("/projets/{projet_id}/lignes/{ligne_id}/commentaires")
    def ajouter(projet_id: int, ligne_id: int, body: CommentaireSaisie, user=Depends(jwt_user)):
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        corps = body.corps.strip()
        if not corps:
            raise HTTPException(status_code=400, detail="Commentaire vide")
        uid, nom = _auteur(user)
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            _ligne_du_projet(cur, projet_id, ligne_id)
            cur.execute(
                "INSERT INTO ad_budget.ligne_commentaires "
                "(ligne_id, projet_id, auteur_id, auteur_nom, corps) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {_COLONNES}",
                (ligne_id, projet_id, uid, nom, corps),
            )
            c = cur.fetchone()
            conn.commit()
            return {"commentaire": c}
        finally:
            conn.close()

    @router.patch("/projets/{projet_id}/commentaires/{commentaire_id}")
    def modifier(projet_id: int, commentaire_id: int, body: CommentaireSaisie,
                 user=Depends(jwt_user)):
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        corps = body.corps.strip()
        if not corps:
            raise HTTPException(status_code=400, detail="Commentaire vide")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            _commentaire_a_soi(cur, projet_id, commentaire_id, user)
            cur.execute(
                "UPDATE ad_budget.ligne_commentaires SET corps = %s, updated_at = NOW() "
                f"WHERE id = %s RETURNING {_COLONNES}",
                (corps, commentaire_id),
            )
            c = cur.fetchone()
            conn.commit()
            return {"commentaire": c}
        finally:
            conn.close()

    @router.delete("/projets/{projet_id}/commentaires/{commentaire_id}")
    def supprimer(projet_id: int, commentaire_id: int, user=Depends(jwt_user)):
        _load_and_authorize_projet(get_conn, projet_id, user, "read")
        conn = get_conn()
        try:
            cur = conn.cursor(row_factory=dict_row)
            c = _commentaire_a_soi(cur, projet_id, commentaire_id, user)
            cur.execute("DELETE FROM ad_budget.ligne_commentaires WHERE id = %s", (commentaire_id,))
            conn.commit()
            return {"ok": True, "ligne_id": c["ligne_id"]}
        finally:
            conn.close()

    return router
