"""
Validation LIVE — scoping sécurité de la présence Ad BUD (lot présence,
août 2026, `modules/bud_presence.py`).

CE QUI EST PROUVÉ ICI (le périmètre CLAUDE.md pour tout ce qui touche
auth / multi-tenant — script serveur OBLIGATOIRE, en premier, Chrome ne le
remplace pas) :

  A. Le PROPRIÉTAIRE du projet peut battre présence (200) et apparaît dans
     son propre roster.
  B. Un MEMBRE de la même organisation, non propriétaire, peut battre
     présence (200) et apparaît À CÔTÉ du propriétaire dans le roster.
  C. Un utilisateur d'une AUTRE organisation reçoit 404 « Projet introuvable »
     — jamais 403, jamais 200 : on ne révèle pas l'existence du projet.
  D. Cet échec 404 n'a PAS pollué le roster — la preuve que l'autorisation
     s'exécute AVANT tout enregistrement de présence (pas après, pas en
     parallèle).
  E. Sur un projet VERROUILLÉ (is_verrouille=TRUE), la présence reste
     accessible (mode='read' n'est jamais bloqué par le verrou — comme la
     détention et le contenu en lecture).
  F. La sortie explicite (DELETE /presence) retire bien l'entrée du roster,
     sans affecter les autres présents.

CE QUI N'EST PAS TESTÉ ICI : la purge par âge (150 s, SEUIL_PRESENCE_SECONDES)
— ce serait un vrai `time.sleep(150)` dans un script live pour reprouver ce
qu'une horloge simulée établit en une fraction de seconde. Couvert par
`tests/test_bud_presence.py` (purs, sans base, sans réseau).

PAS BESOIN DU HUB : la présence est ENTIÈREMENT locale à ad_budget (roster en
mémoire, clé = ad_budget.projets.id, autorisation via
ad_budget.projets.organization_id) — à la différence des scripts
`proof_*_live.py` qui, eux, testent l'articulation avec app_hub. Seuls
DATABASE_URL et JWT_SECRET du service Ad BUD lui-même sont nécessaires.

FIXTURES JETABLES : deux organisations aléatoires (uuid4, PAS Adision ni
Contracta), un projet, trois users ad_budget.users. Le membre et le hors-org
ne sont PRÉ-CRÉÉS NULLE PART : le endpoint les auto-provisionne à leur
premier appel authentifié, exactement comme en production (auto-provision
par courriel, `auth_jwt._provision_user`). Cleanup INTÉGRAL en fin de
script (règle Simon 2026-07-01, self-clean multi-acteurs) — y compris les
users auto-provisionnés en cours de route, pas seulement les fixtures
créées explicitement.

    railway run python scripts/validate_bud_presence_scope.py
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

PASS = FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS — {label}")
    else:
        FAIL += 1
        print(f"FAIL — {label}  {extra}")


def jeton(email, org_uuid):
    payload = {
        "email": email,
        "organization_id": org_uuid,
        "active_organization_id": org_uuid,
        "platform_role": "client",
        "active_role": "user",
        "org_role": None,
        "modules": ["ad_bud"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 1800,
    }
    return pyjwt.encode(payload, SECRET, algorithm="HS256")


def main():
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    email_owner = f"presence-owner-{SUF}@test.fake"
    email_membre = f"presence-membre-{SUF}@test.fake"
    email_horsorg = f"presence-horsorg-{SUF}@test.fake"

    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    cur = conn.cursor()
    projet_id = None
    client = httpx.Client(timeout=30.0)
    try:
        # ── Setup : owner (org A) pré-créé + projet org A ───────────────
        # Membre et hors-org NE SONT PAS pré-créés : voir docstring.
        cur.execute(
            "INSERT INTO ad_budget.users (nom, email, role) VALUES (%s,%s,'user') RETURNING id",
            ("Presence Owner", email_owner),
        )
        owner_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO ad_budget.projets (user_id, organization_id, statut) "
            "VALUES (%s, %s, 'brouillon') RETURNING id",
            (owner_id, org_a),
        )
        projet_id = cur.fetchone()["id"]
        conn.commit()
        check("setup : owner + projet jetable (org A) créés", bool(owner_id and projet_id),
              f"owner_id={owner_id} projet_id={projet_id}")

        h_owner = {"Authorization": "Bearer " + jeton(email_owner, org_a)}
        h_membre = {"Authorization": "Bearer " + jeton(email_membre, org_a)}
        h_horsorg = {"Authorization": "Bearer " + jeton(email_horsorg, org_b)}

        # ── A/B : owner + membre même org, tous deux dans le roster ──────
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_owner)
        check("A. propriétaire -> 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        presents = (r.json() or {}).get("presents") or [] if r.status_code == 200 else []
        check("A2. propriétaire apparaît dans son propre roster",
              any(p_.get("email") == email_owner for p_ in presents), str(presents))

        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_membre)
        check("B. membre de la même org (non propriétaire) -> 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        presents = (r.json() or {}).get("presents") or [] if r.status_code == 200 else []
        emails = {p_.get("email") for p_ in presents}
        check("B2. le roster contient les DEUX présents (pas un de plus, pas un de moins)",
              emails == {email_owner, email_membre}, str(emails))

        # ── C/D : hors organisation -> 404, jamais dans le roster ────────
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_horsorg)
        check("C. utilisateur hors organisation -> 404 (jamais 403, jamais 200)",
              r.status_code == 404, f"{r.status_code} {r.text[:200]}")

        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_owner)
        presents = (r.json() or {}).get("presents") or [] if r.status_code == 200 else []
        emails = {p_.get("email") for p_ in presents}
        check("D. l'échec hors-org n'a PAS pollué le roster (l'autorisation gate AVANT l'enregistrement)",
              email_horsorg not in emails, str(emails))

        # ── E : projet verrouillé -> présence reste lisible ───────────────
        cur.execute("UPDATE ad_budget.projets SET is_verrouille = TRUE WHERE id = %s", (projet_id,))
        conn.commit()
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_membre)
        check("E. projet verrouillé -> présence toujours accessible (200, jamais 409)",
              r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        cur.execute("UPDATE ad_budget.projets SET is_verrouille = FALSE WHERE id = %s", (projet_id,))
        conn.commit()

        # ── F : sortie explicite retire du roster, sans effet sur les autres
        r = client.delete(f"{BUD_URL}/budget/projets/{projet_id}/presence", headers=h_membre)
        check("F. DELETE présence (membre) -> 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_owner)
        presents = (r.json() or {}).get("presents") or [] if r.status_code == 200 else []
        emails = {p_.get("email") for p_ in presents}
        check("F2. le membre parti n'est plus dans le roster, le propriétaire y reste",
              emails == {email_owner}, str(emails))
    finally:
        client.close()
        # ── Self-clean intégral (règle Simon 2026-07-01) : le projet ET
        # TOUS les users touchés, y compris ceux auto-provisionnés par le
        # JWT en cours de script (membre, hors-org) — pas seulement le
        # propriétaire créé explicitement.
        print("\n[Z] Nettoyage")
        try:
            if projet_id:
                cur.execute("DELETE FROM ad_budget.projets WHERE id = %s", (projet_id,))
            cur.execute(
                "DELETE FROM ad_budget.users WHERE email IN (%s,%s,%s)",
                (email_owner, email_membre, email_horsorg),
            )
            conn.commit()
            print(f"  fixtures supprimées : projet {projet_id} + 3 users jetables (owner/membre/hors-org)")
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] cleanup : {e}")
        finally:
            cur.close()
            conn.close()

    print(f"\n{'=' * 60}")
    print(f"=== RÉSULTAT LIVE : {PASS} PASS / {FAIL} FAIL ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
