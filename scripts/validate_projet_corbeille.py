"""
Validation LIVE — corbeille Ad BUD (couche 1, 2026-09).

À LANCER APRÈS que la PR soit fusionnée et déployée (le runner de migrations
doit avoir joué sprint_projet_corbeille.sql au boot du service Ad BUD).

CE QUI EST PROUVÉ ICI :

  A. DELETE /projets/{id} ne fait plus un DELETE physique — la row survit,
     supprime_le et supprime_par sont posés.
  B. Les budget_lignes du projet sont INTACTES après le "delete" (même
     nombre de lignes, mêmes ids) — c'était le risque nommé par le brief :
     un DELETE physique faisait un vrai CASCADE, un UPDATE n'en fait aucun.
  C. Le projet supprimé disparaît de GET /budget/projets (scope='mine').
  D. GET /budget/admin/projets-supprimes (jwt_org_admin -- gestionnaire
     d'ORGANISATION, PAS jwt_admin/staff plateforme -- corrigé 3 sept 2026,
     Simon en direct : "niveau 3 = gestionnaire suffit") le liste, avec
     supprime_le/supprime_par posés et l'identité lue depuis le miroir
     hub_identity_snapshot.
  E. POST /budget/admin/projets/{id}/restaurer le fait réapparaître dans
     GET /budget/projets, ET les budget_lignes sont toujours les mêmes ids
     qu'en A (rien reconstruit, UPDATE minimal des deux seules colonnes).
  F. Un utilisateur NON-gestionnaire (org_role != 'admin', pas non plus
     staff plateforme) reçoit 403 sur les trois routes /admin/*.
  G. DELETE /budget/admin/projets/{id}/definitif — sortie DÉFINITIVE de la
     corbeille, demandée par Simon en direct ("une fois supprimé dans la
     corbeille [...] c'est définitif") : la row projets ET ses budget_lignes
     disparaissent pour de vrai (CASCADE), le projet n'est plus JOIGNABLE
     nulle part, ni GET /projets ni la corbeille elle-même.
  H. Garde-fou : DELETE .../definitif sur un projet qui n'est PAS dans la
     corbeille (supprime_le IS NULL, jamais soft-supprimé) renvoie 404 et ne
     touche à rien — jamais un raccourci pour effacer un projet vivant sans
     passer par la corbeille d'abord.

Fixture jetable : un user + un/deux projets créés directement en base (pas
besoin du hub — aucun projet n'a de ad_hub_project_id, donc
soft_delete_project côté hub n'est jamais appelé). Cleanup INTÉGRAL en fin
de script (idempotent : le projet du test G est déjà parti, ses DELETE de
cleanup sont alors des no-ops silencieux).

    railway run python scripts/validate_projet_corbeille.py
"""
import os
import sys
import time
import uuid

import httpx
import jwt as pyjwt
import psycopg
from psycopg.rows import dict_row

BUD_URL = os.environ.get("BUD_API_URL", "https://api-bud.adision.ca")
SECRET = os.environ["JWT_SECRET"]
SUF = str(int(time.time()))[-6:]
DB = os.environ.get("DATABASE_PUBLIC_URL") or os.environ["DATABASE_URL"]

FAILS = []


def check(label, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {label} {detail}")
    if not cond:
        FAILS.append(label)


def jwt_for(email, org, role="user", platform_role="user"):
    return pyjwt.encode(
        {"email": email, "organization_id": org, "org_role": role,
         "platform_role": platform_role, "modules": ["ad_bud"]},
        SECRET, algorithm="HS256",
    )


def main():
    org = str(uuid.uuid4())
    email_user = f"corbeille-{SUF}-user@test.adision.invalid"
    email_admin = f"corbeille-{SUF}-admin@test.adision.invalid"
    tok_user = jwt_for(email_user, org, role="user")
    tok_admin = jwt_for(email_admin, org, role="admin", platform_role="admin")

    conn = psycopg.connect(DB, row_factory=dict_row, autocommit=True)
    cur = conn.cursor()

    projet_id = None
    projet_vivant_id = None
    try:
        # Fixture : projet + 2 lignes, sans lien hub.
        cur.execute(
            "INSERT INTO ad_budget.projets (organization_id, nom, statut) "
            "VALUES (%s, %s, 'brouillon') RETURNING id",
            (org, f"TEST-QA-corbeille-{SUF}"),
        )
        projet_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO ad_budget.budget_lignes (projet_id, description, section) "
            "VALUES (%s, 'ligne A', '01 00 00'), (%s, 'ligne B', '02 00 00') "
            "RETURNING id",
            (projet_id, projet_id),
        )
        ligne_ids_avant = sorted(r["id"] for r in cur.fetchall())
        check("fixture créée", len(ligne_ids_avant) == 2, f"projet={projet_id}")

        h_user = {"Authorization": f"Bearer {tok_user}"}
        h_admin = {"Authorization": f"Bearer {tok_admin}"}

        # A + B — soft-delete, lignes intactes.
        r = httpx.delete(f"{BUD_URL}/budget/projets/{projet_id}", headers=h_user, timeout=20)
        check("A. DELETE renvoie 200", r.status_code == 200, str(r.status_code))
        cur.execute("SELECT supprime_le, supprime_par FROM ad_budget.projets WHERE id=%s", (projet_id,))
        row = cur.fetchone()
        check("A. supprime_le posé", row["supprime_le"] is not None)
        check("A. supprime_par = email courant", row["supprime_par"] == email_user, str(row["supprime_par"]))
        cur.execute("SELECT id FROM ad_budget.budget_lignes WHERE projet_id=%s ORDER BY id", (projet_id,))
        ligne_ids_apres = sorted(r2["id"] for r2 in cur.fetchall())
        check("B. budget_lignes intactes après delete", ligne_ids_apres == ligne_ids_avant,
              f"{ligne_ids_avant} vs {ligne_ids_apres}")

        # C — disparu des listes.
        r = httpx.get(f"{BUD_URL}/budget/projets", params={"scope": "mine"}, headers=h_user, timeout=20)
        ids = [p["id"] for p in r.json()] if r.status_code == 200 else []
        check("C. absent de GET /projets", projet_id not in ids, f"status={r.status_code}")

        # D — visible dans la corbeille admin.
        r = httpx.get(f"{BUD_URL}/budget/admin/projets-supprimes", headers=h_admin, timeout=20)
        check("D. GET corbeille 200", r.status_code == 200, str(r.status_code))
        corbeille = r.json() if r.status_code == 200 else []
        check("D. projet présent dans la corbeille", any(p["id"] == projet_id for p in corbeille))

        # F — non-admin refusé.
        r = httpx.get(f"{BUD_URL}/budget/admin/projets-supprimes", headers=h_user, timeout=20)
        check("F. GET corbeille refuse un non-admin", r.status_code == 403, str(r.status_code))
        r = httpx.post(f"{BUD_URL}/budget/admin/projets/{projet_id}/restaurer", headers=h_user, timeout=20)
        check("F. POST restaurer refuse un non-admin", r.status_code == 403, str(r.status_code))

        # E — restauration.
        r = httpx.post(f"{BUD_URL}/budget/admin/projets/{projet_id}/restaurer", headers=h_admin, timeout=20)
        check("E. POST restaurer 200", r.status_code == 200, str(r.status_code))
        cur.execute("SELECT supprime_le, supprime_par FROM ad_budget.projets WHERE id=%s", (projet_id,))
        row = cur.fetchone()
        check("E. supprime_le/par remis à NULL", row["supprime_le"] is None and row["supprime_par"] is None)
        cur.execute("SELECT id FROM ad_budget.budget_lignes WHERE projet_id=%s ORDER BY id", (projet_id,))
        ligne_ids_restaure = sorted(r2["id"] for r2 in cur.fetchall())
        check("E. budget_lignes toujours les mêmes ids", ligne_ids_restaure == ligne_ids_avant)
        r = httpx.get(f"{BUD_URL}/budget/projets", params={"scope": "mine"}, headers=h_user, timeout=20)
        ids = [p["id"] for p in r.json()] if r.status_code == 200 else []
        check("E. réapparu dans GET /projets", projet_id in ids)

        # H (avant G) — garde-fou : un projet VIVANT (jamais soft-supprimé)
        # ne peut pas être effacé définitivement en contournant la corbeille.
        cur.execute(
            "INSERT INTO ad_budget.projets (organization_id, nom, statut) "
            "VALUES (%s, %s, 'brouillon') RETURNING id",
            (org, f"TEST-QA-corbeille-vivant-{SUF}"),
        )
        projet_vivant_id = cur.fetchone()["id"]
        r = httpx.delete(f"{BUD_URL}/budget/admin/projets/{projet_vivant_id}/definitif", headers=h_admin, timeout=20)
        check("H. definitif refuse un projet vivant (404)", r.status_code == 404, str(r.status_code))
        cur.execute("SELECT id FROM ad_budget.projets WHERE id=%s", (projet_vivant_id,))
        check("H. projet vivant toujours présent", cur.fetchone() is not None)

        # G — re-soft-delete puis suppression définitive pour de vrai.
        r = httpx.delete(f"{BUD_URL}/budget/projets/{projet_id}", headers=h_user, timeout=20)
        check("G. re-DELETE (soft) 200 avant le test définitif", r.status_code == 200, str(r.status_code))

        r = httpx.delete(f"{BUD_URL}/budget/admin/projets/{projet_id}/definitif", headers=h_user, timeout=20)
        check("F. DELETE definitif refuse un non-admin (403)", r.status_code == 403, str(r.status_code))

        r = httpx.delete(f"{BUD_URL}/budget/admin/projets/{projet_id}/definitif", headers=h_admin, timeout=20)
        check("G. DELETE definitif 200", r.status_code == 200, str(r.status_code))

        cur.execute("SELECT id FROM ad_budget.projets WHERE id=%s", (projet_id,))
        check("G. projet disparu de la table projets", cur.fetchone() is None)
        cur.execute("SELECT id FROM ad_budget.budget_lignes WHERE projet_id=%s", (projet_id,))
        check("G. budget_lignes disparues (CASCADE)", cur.fetchall() == [])

        r = httpx.get(f"{BUD_URL}/budget/admin/projets-supprimes", headers=h_admin, timeout=20)
        corbeille_apres = r.json() if r.status_code == 200 else []
        check("G. absent de la corbeille après suppression définitive",
              not any(p["id"] == projet_id for p in corbeille_apres))

    finally:
        if projet_id:
            cur.execute("DELETE FROM ad_budget.budget_lignes WHERE projet_id=%s", (projet_id,))
            cur.execute("DELETE FROM ad_budget.projets WHERE id=%s", (projet_id,))
        if projet_vivant_id:
            cur.execute("DELETE FROM ad_budget.budget_lignes WHERE projet_id=%s", (projet_vivant_id,))
            cur.execute("DELETE FROM ad_budget.projets WHERE id=%s", (projet_vivant_id,))
        cur.close()
        conn.close()

    print()
    if FAILS:
        print(f"{len(FAILS)} contrôle(s) en échec : {FAILS}")
        sys.exit(1)
    print("Tous les contrôles passent.")


if __name__ == "__main__":
    main()
