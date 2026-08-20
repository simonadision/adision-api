"""DÉCOUVERTE (chantier « tests d'invariant sur les chiffres que Simon voit »,
20 août 2026) — pas un des 4 invariants demandés par le brief, un sous-produit
DIRECT de l'Invariant B : en construisant la fixture partagée de l'incident
(isolant réel, projet 290) pour comparer Python == JS, ce même cas exécuté
contre le mirroir JS de l'EMPREINTE budget (budgetFingerprint.js::lineTotal,
DISTINCT de getRow/computeLigneTotal) révèle qu'il n'a JAMAIS reçu le
correctif production_valeur d'a07c72b.

Constat empirique (isolant réel, 1 800 pi² à 96 pi²/h, heures stockée 149,40
— cf. test_invariant_b_ligne_python_js.py::test_isolant_incident_290_valeur_attendue) :
  Python (_line_total, SOURCE UNIQUE depuis a07c72b)        : 5 440,00 $
  JS empreinte (budgetFingerprint.js::lineTotal, INCHANGÉ)  : 11 970,00 $
  écart                                                     : 6 530,00 $ sur UNE ligne.

Cause : budgetFingerprint.js::heuresEffectives (adision-monorepo/apps/ad-bud/
src/utils/budgetFingerprint.js) n'a pas de paramètre production_valeur — il a
été écrit (c2f62c94) avant le mécanisme de ratio de production (14 août) et
n'a pas été mis à jour quand a07c72b a posé la priorité absolue côté serveur.
budgetFingerprint.js prétend pourtant, dans son propre commentaire, être
« EXACTEMENT comme compute_budget_totals / getRow ».

Portée du risque : l'empreinte sert à détecter, à l'émission d'un devis
(Option B), si le budget a changé depuis sa génération — SANS bloquer (cf. la
docstring de budget_fingerprint.py). Toute ligne de devis avec
production_valeur renseignée (278 lignes / 17 projets au 20 août 2026, cf.
a07c72b) fait diverger l'empreinte CLIENT (générée avec le mirroir JS boiteux)
de l'empreinte SERVEUR (recalculée à l'émission avec _line_total, correct) —
FAUX POSITIF « le budget a changé » systématique sur ces devis.

CE FICHIER NE CORRIGE RIEN — décision explicite du chantier : « si un
invariant révèle une divergence réelle en production, ARRÊTE et expose-la —
c'est une découverte qui mérite une décision de Simon, pas un correctif
silencieux ». xfail STRICT (même patron que test_divergences_ecran_rapport.py,
D3/D4) : si budgetFingerprint.js reçoit un jour le correctif sans qu'on retire
ce marqueur, ce test XPASS et le fait savoir bruyamment plutôt que de
disparaître en silence.

Portée CI : comme test_invariant_b_ligne_python_js.py, ce test a besoin du
dépôt frère adision-monorepo + node -> réservé au pre-push local, skip ailleurs.

Lancer : pytest tests/test_budget_fingerprint_production_valeur_discovery.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from modules.budget_fingerprint import _line_total  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.dirname(os.path.dirname(HERE))
JS_CLI = os.path.join(RD, "adision-monorepo", "apps", "ad-bud", "src", "utils", "budgetFingerprint.node.mjs")
_DISPONIBLE = os.path.isfile(JS_CLI)
_RAISON_SKIP = "dépôt frère adision-monorepo ou node absent — réservé au pre-push local"

# Cas RÉEL de l'incident 2026-08-20 (projet 290), même ligne que
# test_invariant_b_ligne_python_js.py::test_isolant_incident_290_valeur_attendue.
_LIGNE_ISOLANT = {
    "id": 1, "unite": "pi2", "qte": 1800, "prix_unitaire": 2.5,
    "heures": 149.40, "heures_manuelles": False, "taux_horaire": 50,
    "ajust_materiaux": 0, "ajust_main_oeuvre": 0, "sous_traitant_montant": 0,
    "ajust_sous_traitant": 0, "ajustement_pct": 0, "production_valeur": 96,
}


def _js_line_total_cents():
    """budgetFingerprint.js n'exporte pas lineTotal (privé) — seule sa sortie
    condensée (canonicalString) l'est. On isole le total de la ligne unique en
    cents depuis la dernière ligne de la chaîne canonique ("<id>=<cents>")."""
    fx = {"projet": {}, "lignes": [_LIGNE_ISOLANT], "montant": 0}
    p = os.path.join(HERE, "_tmp_discovery_fixture.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(fx, fh)
    try:
        r = subprocess.run(["node", JS_CLI, p], capture_output=True, text=True, encoding="utf-8")
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
    if r.returncode != 0:
        raise RuntimeError(f"node CLI (budgetFingerprint.node.mjs) a échoué : {r.stderr.strip()[:300]}")
    canonical = json.loads(r.stdout)["canonical"]
    derniere = canonical.strip().split("\n")[-1]
    return int(derniere.split("=")[1])


@pytest.mark.skipif(not _DISPONIBLE, reason=_RAISON_SKIP)
@pytest.mark.xfail(strict=True, reason=(
    "DÉCOUVERTE 2026-08-20 (chantier tests d'invariant) : budgetFingerprint.js::"
    "heuresEffectives ignore production_valeur — jamais mis à jour par a07c72b, qui n'a "
    "touché que le serveur. En attente de décision Simon (cf. docstring du fichier). "
    "Retirer ce marqueur seulement quand budgetFingerprint.js appliquera la même priorité "
    "production_valeur que modules/aggregates.py::heures_effectives."
))
def test_empreinte_js_diverge_de_python_sur_production_valeur():
    total_py_cents = round(_line_total(False, _LIGNE_ISOLANT) * 100)
    total_js_cents = _js_line_total_cents()
    assert total_py_cents == total_js_cents, (
        f"Python={total_py_cents / 100:.2f} $ vs JS(empreinte)={total_js_cents / 100:.2f} $ "
        f"— écart {(total_js_cents - total_py_cents) / 100:.2f} $ sur la ligne isolant "
        "de l'incident 2026-08-20 (production_valeur=96)")


if __name__ == "__main__":
    if not _DISPONIBLE:
        print(f"[SKIP] {_RAISON_SKIP}")
        sys.exit(0)
    total_py_cents = round(_line_total(False, _LIGNE_ISOLANT) * 100)
    total_js_cents = _js_line_total_cents()
    if total_py_cents == total_js_cents:
        print("[XPASS inattendu] l'écart a disparu — retirer le xfail de ce fichier.")
        sys.exit(1)
    print(f"[XFAIL attendu] Python={total_py_cents / 100:.2f} $ JS={total_js_cents / 100:.2f} $ "
          f"écart={(total_js_cents - total_py_cents) / 100:.2f} $")
    sys.exit(0)
