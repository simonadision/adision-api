"""Validation serveur — LE RELAIS d'Ad BUD vers Ad EST, en bout en bout.

Le script de la base (adision-est-api/scripts/validate_csi_titres_lecture.py)
prouve que la REQUÊTE ne lève plus. Celui-ci prouve la seule chose qu'il ne
peut pas prouver : que la ROUTE, en production, derrière le relais et
l'autorisation projet, rend bien 200 et les titres attendus.

    GET https://api-bud.adision.ca/budget/csi-titres?projet_id=279
      → adision-api (autorisation projet, refs ad_bud:)
      → adision-est-api /reference/csi-titres  (résolution + scope org)

CE SCRIPT N'ÉCRIT AUCUNE DONNÉE. Un GET, rien d'autre.

SECRETS : `JWT_SECRET` est lu depuis os.environ pour signer un jeton de
lecture de courte durée (2 minutes). Sa valeur n'est jamais imprimée, ni
écrite, ni retournée — seule sa PRÉSENCE est rapportée. Le jeton porte
l'identité réelle de la personne qui tient le budget visé (lue en base),
sinon l'autorisation projet refuserait et on ne prouverait rien.

DEUX `railway run` IMBRIQUÉS, ET C'EST À CONTRECŒUR. `JWT_SECRET` vit sur le
service `web`, mais son `DATABASE_URL` est l'hôte INTERNE de Railway, qui ne
se résout pas depuis un poste. Poser `DATABASE_PUBLIC_URL =
${{Postgres.DATABASE_PUBLIC_URL}}` sur `web` rendrait le second `railway run`
inutile — c'est un réglage de dashboard, donc du ressort de Simon.

    railway run --service web railway run --service Postgres \\
        python scripts/validate_csi_titres_relais.py
    (dans le projet Railway adision-bud)
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

resultats = []


def controle(nom, condition, detail=""):
    resultats.append((nom, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'} - {nom}{(' | ' + detail) if detail else ''}")


def main():
    secret = os.environ.get("JWT_SECRET") or ""
    controle("0. JWT_SECRET est injecte (presence seule, valeur jamais affichee)",
             bool(secret), f"{len(secret)} caracteres" if secret else "ABSENT")
    if not secret:
        return 1

    url = os.environ.get("DATABASE_URL") or ""
    if not url or "railway.internal" in url:
        url = os.environ.get("DATABASE_PUBLIC_URL") or url
    if not url:
        print("FAIL - DATABASE_URL absent.")
        return 1

    conn = psycopg.connect(url)
    cur = conn.cursor(row_factory=dict_row)
    try:
        cur.execute(
            # ad_budget.projets ne porte PAS de nom : l'identite lisible d'un
            # budget vit dans Ad HUB. On se repere donc par l'id, qui suffit.
            #
            # DEUX IDENTITES, ET ON PREND LA BONNE. Le budget 279 appartient a
            # simon@contracta.ca mais c'est simon@adision.ca qui le TIENT et
            # qui a pose les titres. `detenteur_id` porte l'id HUB — le seul
            # espace d'identifiants que le JWT et Ad EST comprennent ; `user_id`
            # est l'id LOCAL d'ad_budget et ne vaut rien hors de cette base
            # (piege deja paye quatre fois dans ce module). On signe donc au nom
            # du detenteur quand il y en a un.
            "SELECT p.id, p.organization_id, p.source_gabarit_id, "
            "       p.detenteur_id, p.detenteur_email, u.email AS proprietaire "
            "FROM ad_budget.projets p JOIN ad_budget.users u ON u.id = p.user_id "
            "WHERE p.id = %s",
            (PROJET_ID,),
        )
        p = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not p:
        print(f"FAIL - budget {PROJET_ID} introuvable.")
        return 1
    controle(f"1. le budget {PROJET_ID} existe et n'est ne d'AUCUN gabarit "
             f"(le cas qui plantait)", p["source_gabarit_id"] is None,
             f"proprietaire {p['proprietaire']}, "
             f"source_gabarit_id={p['source_gabarit_id']}")

    email = p["detenteur_email"] or p["proprietaire"]
    user_id = p["detenteur_id"]
    controle("2. une identite HUB est disponible pour signer le jeton",
             bool(user_id), f"{email} (user_id hub {user_id})")
    if not user_id:
        print("       Ad EST refuse un JWT sans user_id : sans identite hub, "
              "le controle ne peut pas etre joue.")
        return 1

    maintenant = int(time.time())
    jeton = jwt.encode(
        {
            "user_id": user_id,
            "email": email,
            "organization_id": str(p["organization_id"]),
            "modules": ["ad_bud", "ad_est"],
            "platform_role": "super_admin",
            "iat": maintenant,
            "exp": maintenant + 120,   # 2 minutes : le temps du controle
        },
        secret,
        algorithm="HS256",
    )

    with httpx.Client(timeout=20.0) as client:
        r = client.get(f"{API}/budget/csi-titres",
                       params={"projet_id": PROJET_ID},
                       headers={"Authorization": f"Bearer {jeton}"})

    controle(f"3. GET /budget/csi-titres?projet_id={PROJET_ID} rend 200",
             r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"       corps : {r.text[:400]}")
        return 1

    d = r.json()
    titres = d.get("titres") or {}
    controle("4. la reponse porte les titres personnalises",
             len(titres) > 0, f"{len(titres)} code(s)")
    for code in sorted(titres):
        v = titres[code]
        print(f"       {code} : « {v.get('titre')} »  (portee {v.get('portee')}, "
              f"officiel « {v.get('officiel')} »)")
    controle("5. chaque titre voyage AVEC son titre officiel (la recherche "
             "trouve encore la section par son vrai nom)",
             all(v.get("officiel") for v in titres.values()))
    controle("6. le detail par portee est present (bouton de remontee)",
             isinstance(d.get("par_portee"), list),
             f"{len(d.get('par_portee') or [])} ligne(s)")
    controle("7. aucun code CSI n'a ete reecrit : les cles sont des codes "
             "MasterFormat bien formes",
             all(len(c) == 8 and c[2] == ' ' and c[5] == ' ' for c in titres),
             ", ".join(sorted(titres)) or "aucun")

    rates = [n for n, ok in resultats if not ok]
    print(f"\n{'=' * 62}")
    print(f"{len(resultats) - len(rates)}/{len(resultats)} controles PASS")
    for n in rates:
        print(f"  ECHEC : {n}")
    return 1 if rates else 0


if __name__ == "__main__":
    sys.exit(main())
