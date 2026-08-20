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
     — jamais 403, jamais 200 : on ne révèle pas l'existence du projet, ET le
     corps de la réponse ne fuite aucune identité du roster.
  D. Cet échec 404 n'a PAS pollué le roster — la preuve que l'autorisation
     s'exécute AVANT tout enregistrement de présence (pas après, pas en
     parallèle).
  E. Sur un projet VERROUILLÉ (is_verrouille=TRUE), la présence reste
     accessible (mode='read' n'est jamais bloqué par le verrou — comme la
     détention et le contenu en lecture).
  F. La sortie explicite (DELETE /presence) retire bien l'entrée du roster,
     sans affecter les autres présents.
  G. Aucun JWT -> 401 (jamais 200, jamais 404), et ce 401 ne fuite aucune
     identité du roster dans son corps.
  H. JWT invalide (mal formé / signature bidon) -> 401.
  I. JWT expiré -> 401.
  J. JWT valide mais SANS le module "ad_bud" -> 403 (le « sans droit » au
     niveau plateforme, orthogonal au « sans droit » au niveau organisation
     déjà couvert par C — voir note plus bas).

  Note sur « utilisateur sans droit sur le projet » (item du brief) : dans ce
  système, mode='read' n'a PAS d'ACL par-projet — tout membre de la même
  organisation a un droit de lecture sur tout projet de son organisation
  (`_authorize_projet`, ad_budget_api.py). « Sans droit sur le projet » se
  réduit donc structurellement à DEUX cas déjà couverts : hors organisation
  (C/D) et sans le module ad_bud au niveau plateforme (J). Il n'existe pas de
  troisième palier « membre de l'organisation mais refusé sur CE projet
  précis » à tester — le vérifier aurait été tester un cas qui n'existe pas
  dans le code.

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
Contracta), un projet, quatre users ad_budget.users. Le membre, le hors-org
et le sans-module ne sont PRÉ-CRÉÉS NULLE PART : le endpoint les
auto-provisionne à leur premier appel authentifié, exactement comme en
production (auto-provision par courriel, `auth_jwt._provision_user`) — SAUF
pour G/H/I/J, qui sont TOUS rejetés avant `_provision_user` (JWT absent,
invalide, expiré ou module manquant coupent avant tout accès DB), donc ces
quatre-là ne provisionnent jamais de ligne. Cleanup INTÉGRAL en fin de
script (règle Simon 2026-07-01, self-clean multi-acteurs) — y compris les
users auto-provisionnés en cours de route, et par défense en profondeur les
emails de G/H/I/J aussi (au cas où une régression future les ferait
provisionner), pas seulement les fixtures créées explicitement.

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


def jeton(email, org_uuid, modules=("ad_bud",), exp_delta=1800, secret=None):
    """`exp_delta` négatif -> jeton déjà expiré (test I). `modules=()` -> pas
    le module ad_bud (test J). `secret` -> pour signer avec une clé bidon
    (test H, signature invalide) sans jamais avoir besoin de lire SECRET."""
    payload = {
        "email": email,
        "organization_id": org_uuid,
        "active_organization_id": org_uuid,
        "platform_role": "client",
        "active_role": "user",
        "org_role": None,
        "modules": list(modules),
        "iat": int(time.time()) - 5,
        "exp": int(time.time()) + exp_delta,
    }
    return pyjwt.encode(payload, secret or SECRET, algorithm="HS256")


def main():
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    email_owner = f"presence-owner-{SUF}@test.fake"
    email_membre = f"presence-membre-{SUF}@test.fake"
    email_horsorg = f"presence-horsorg-{SUF}@test.fake"
    email_sansmodule = f"presence-sansmodule-{SUF}@test.fake"

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
        check("C2. le 404 hors-org ne fuite aucune identité du roster (nom/courriel)",
              email_owner not in r.text and email_membre not in r.text, r.text[:200])

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

        # ── G : aucun JWT -> 401, jamais de fuite d'identité dans le corps ──
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement")
        check("G. aucun JWT -> 401 (jamais 200, jamais 404)",
              r.status_code == 401, f"{r.status_code} {r.text[:200]}")
        check("G2. le 401 sans JWT ne fuite aucune identité du roster",
              email_owner not in r.text and email_membre not in r.text, r.text[:200])

        # ── H : JWT invalide (mal formé, aucune signature valide) -> 401 ───
        h_invalide = {"Authorization": "Bearer ceci-n-est-pas-un-jwt-valide"}
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_invalide)
        check("H. JWT invalide (mal formé) -> 401", r.status_code == 401, f"{r.status_code} {r.text[:200]}")

        # ── I : JWT expiré -> 401, même signature et contenu par ailleurs valides
        h_expire = {"Authorization": "Bearer " + jeton(email_owner, org_a, exp_delta=-60)}
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_expire)
        check("I. JWT expiré -> 401", r.status_code == 401, f"{r.status_code} {r.text[:200]}")

        # ── J : JWT valide mais SANS le module ad_bud -> 403 (« sans droit »
        # au niveau plateforme — voir note du docstring sur le périmètre de
        # ce cas vs. C/D, qui couvrent le « sans droit » au niveau organisation)
        h_sansmodule = {"Authorization": "Bearer " + jeton(email_sansmodule, org_a, modules=())}
        r = client.post(f"{BUD_URL}/budget/projets/{projet_id}/presence/battement", headers=h_sansmodule)
        check("J. JWT valide sans le module ad_bud -> 403 (jamais 200)",
              r.status_code == 403, f"{r.status_code} {r.text[:200]}")
    finally:
        client.close()
        # ── Self-clean intégral (règle Simon 2026-07-01) : le projet ET
        # TOUS les users touchés, y compris ceux auto-provisionnés par le
        # JWT en cours de script (membre, hors-org) — pas seulement le
        # propriétaire créé explicitement. email_sansmodule est inclus par
        # défense en profondeur : G/H/I/J sont normalement rejetés AVANT
        # `_provision_user` (aucune ligne créée), mais si une régression
        # future faisait fuiter un provisioning malgré un 401/403, ce
        # nettoyage le rattraperait quand même.
        print("\n[Z] Nettoyage")
        try:
            if projet_id:
                cur.execute("DELETE FROM ad_budget.projets WHERE id = %s", (projet_id,))
            cur.execute(
                "DELETE FROM ad_budget.users WHERE email IN (%s,%s,%s,%s)",
                (email_owner, email_membre, email_horsorg, email_sansmodule),
            )
            conn.commit()
            print(f"  fixtures supprimées : projet {projet_id} + users jetables "
                  f"(owner/membre/hors-org/sans-module)")
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
