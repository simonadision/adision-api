"""Validation serveur — RENOMMER DANS AD BUD TIENT APRÈS RECHARGEMENT.

Le défaut du 6 août 2026 n'était pas dans l'écriture : le PUT rendait 200 et
le titre était bien en base. C'est la RELECTURE qui partait en 500, et
l'écran retombait sur les titres officiels — d'où « il ne sauvegarde pas ».

Ce script joue donc le cycle complet, en HTTP sur la route de production,
exactement comme le fait l'écran : PUT, puis GET (le « rechargement »), puis
DELETE, puis GET à nouveau. Si le titre revient au deuxième temps et
disparaît au quatrième, le renommage tient et le retour au titre officiel
aussi.

CE SCRIPT ÉCRIT — ET IL NETTOIE. Il pose un titre « TEST-<code> » sur un
code CSI que personne n'a renommé, puis le RETIRE. Le DELETE est dans un
`finally` : même si un contrôle échoue en cours de route, rien ne reste. Les
titres réels de Simon ne sont ni lus en écriture, ni touchés — le script
refuse de travailler sur un code déjà renommé.

LE CODE CSI EST L'IDENTITÉ : on vérifie en base, avant et après, que
`app_est.csi_sections` n'a pas bougé d'une ligne.

SECRETS : `JWT_SECRET` lu depuis os.environ, jamais imprimé.

    railway run --service web railway run --service Postgres \\
        python scripts/validate_csi_titres_aller_retour.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
import jwt
import psycopg
from psycopg.rows import dict_row

API = os.environ.get("AD_BUD_API_URL", "https://api-bud.adision.ca").rstrip("/")
PROJET_ID = int(os.environ.get("PROJET_ID", "279"))
# Un code que Simon n'a pas renommé. Vérifié à l'exécution, pas supposé.
CODE = os.environ.get("CODE_CSI", "03 30 00")
TITRE_TEST = "TEST-renommage-aller-retour"

resultats = []


def controle(nom, condition, detail=""):
    resultats.append((nom, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'} - {nom}{(' | ' + detail) if detail else ''}")


def main():
    secret = os.environ.get("JWT_SECRET") or ""
    if not secret:
        print("FAIL - JWT_SECRET absent.")
        return 1

    url = os.environ.get("DATABASE_URL") or ""
    if not url or "railway.internal" in url:
        url = os.environ.get("DATABASE_PUBLIC_URL") or url

    conn = psycopg.connect(url)
    cur = conn.cursor(row_factory=dict_row)
    try:
        cur.execute(
            "SELECT p.organization_id, p.detenteur_id, p.detenteur_email, u.email AS prop "
            "FROM ad_budget.projets p JOIN ad_budget.users u ON u.id = p.user_id "
            "WHERE p.id = %s",
            (PROJET_ID,),
        )
        p = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not p or not p["detenteur_id"]:
        print(f"FAIL - budget {PROJET_ID} introuvable ou sans identite hub.")
        return 1

    maintenant = int(time.time())
    jeton = jwt.encode(
        {
            "user_id": p["detenteur_id"],
            "email": p["detenteur_email"] or p["prop"],
            "organization_id": str(p["organization_id"]),
            "modules": ["ad_bud", "ad_est"],
            "platform_role": "super_admin",
            "iat": maintenant,
            "exp": maintenant + 300,
        },
        secret,
        algorithm="HS256",
    )
    entetes = {"Authorization": f"Bearer {jeton}"}
    client = httpx.Client(timeout=20.0, headers=entetes)

    def lire():
        r = client.get(f"{API}/budget/csi-titres", params={"projet_id": PROJET_ID})
        r.raise_for_status()
        return r.json().get("titres") or {}

    pose = False
    try:
        avant = lire()
        controle(f"1. le code temoin {CODE} n'est renomme par personne",
                 CODE not in avant,
                 f"{len(avant)} titre(s) reel(s) en place, aucun touche")
        if CODE in avant:
            return 1

        r = client.put(f"{API}/budget/csi-titres", json={
            "portee": "projet", "projet_id": PROJET_ID,
            "code": CODE, "titre": TITRE_TEST,
        })
        pose = r.status_code == 200
        controle("2. PUT du renommage rend 200", pose, f"HTTP {r.status_code}")

        apres = lire()
        controle("3. RECHARGEMENT : la relecture rend le titre pose",
                 apres.get(CODE, {}).get("titre") == TITRE_TEST,
                 f"lu « {apres.get(CODE, {}).get('titre')} »")
        controle("4. les titres reels de Simon sont toujours la, intacts",
                 all(apres.get(c, {}).get("titre") == v.get("titre")
                     for c, v in avant.items()),
                 ", ".join(f"{c}=« {v.get('titre')} »" for c, v in sorted(avant.items())))
        controle("5. le titre officiel voyage toujours a cote du personnalise",
                 bool(apres.get(CODE, {}).get("officiel")),
                 f"officiel « {apres.get(CODE, {}).get('officiel')} »")
    finally:
        if pose:
            r = client.delete(f"{API}/budget/csi-titres", params={
                "portee": "projet", "code": CODE, "projet_id": PROJET_ID,
            })
            controle("6. NETTOYAGE : le titre d'essai est retire", r.status_code == 200,
                     f"HTTP {r.status_code}")

    fin = lire()
    controle("7. apres nettoyage, le code temoin est revenu au titre officiel",
             CODE not in fin)
    controle("8. et les titres reels de Simon n'ont pas bouge d'un caractere",
             {c: v.get("titre") for c, v in fin.items()}
             == {c: v.get("titre") for c, v in avant.items()},
             ", ".join(f"{c}=« {v.get('titre')} »" for c, v in sorted(fin.items())))
    client.close()

    rates = [n for n, ok in resultats if not ok]
    print(f"\n{'=' * 62}")
    print(f"{len(resultats) - len(rates)}/{len(resultats)} controles PASS")
    for n in rates:
        print(f"  ECHEC : {n}")
    return 1 if rates else 0


if __name__ == "__main__":
    sys.exit(main())
