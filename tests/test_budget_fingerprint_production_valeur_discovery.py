"""CORRECTIF appliqué (chantier « tests d'invariant sur les chiffres que Simon
voit », 20 août 2026) — verrou de non-régression, plus une découverte.

Historique : ce fichier documentait une découverte, sous-produit DIRECT de
l'Invariant B — en construisant la fixture partagée de l'incident (isolant
réel, projet 290) pour comparer Python == JS, ce même cas exécuté contre le
mirroir JS de l'EMPREINTE budget (budgetFingerprint.js::lineTotal, DISTINCT de
getRow/computeLigneTotal) révélait qu'il n'avait JAMAIS reçu le correctif
production_valeur d'a07c72b — écart mesuré de 6 530,00 $ sur cette seule ligne
(Python 5 440,00 $ vs JS empreinte 11 970,00 $). Le test tournait `xfail
strict`, en attente d'une décision de Simon (cf. commentaire du chantier
« si un invariant révèle une divergence réelle en production, ARRÊTE et
expose-la »).

Décision de Simon : corriger. budgetFingerprint.js (adision-monorepo/apps/
ad-bud/src/utils/) n'a plus sa propre copie de la règle des heures — sa
fonction `condense()` délègue désormais à `computeLigneTotal`
(budgetLigneTotal.js), LA MÊME fonction que App.jsx::getRow appelle à
l'écran et que ce test compare au calcul Python (_line_total). Même
traitement appliqué à adaptBudgetLines.js (agrégats dashboard / Ad ANA),
signalé le même jour avec le même défaut.

Ce test n'est plus `xfail` : il verrouille l'accord retrouvé, sur le CAS RÉEL
de l'incident. S'il redevenait rouge, ce serait une régression du correctif
lui-même — pas une découverte à documenter.

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
    """budgetFingerprint.js n'exporte pas lineTotal (délégué à computeLigneTotal,
    privé à ce module) — seule sa sortie condensée (canonicalString) l'est. On
    isole le total de la ligne unique en cents depuis la dernière ligne de la
    chaîne canonique ("<id>=<cents>")."""
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
def test_empreinte_js_concorde_avec_python_sur_production_valeur():
    total_py_cents = round(_line_total(False, _LIGNE_ISOLANT) * 100)
    total_js_cents = _js_line_total_cents()
    assert total_py_cents == total_js_cents, (
        f"Python={total_py_cents / 100:.2f} $ vs JS(empreinte)={total_js_cents / 100:.2f} $ "
        f"— écart {(total_js_cents - total_py_cents) / 100:.2f} $ sur la ligne isolant "
        "de l'incident 2026-08-20 (production_valeur=96) — régression du correctif "
        "production_valeur dans budgetFingerprint.js")
    # Chiffre exact attendu (critère d'acceptation #1 du chantier) : 5 440,00 $.
    assert total_py_cents == 544000


if __name__ == "__main__":
    if not _DISPONIBLE:
        print(f"[SKIP] {_RAISON_SKIP}")
        sys.exit(0)
    total_py_cents = round(_line_total(False, _LIGNE_ISOLANT) * 100)
    total_js_cents = _js_line_total_cents()
    if total_py_cents != total_js_cents:
        print(f"[FAIL] Python={total_py_cents / 100:.2f} $ JS={total_js_cents / 100:.2f} $ "
              f"écart={(total_js_cents - total_py_cents) / 100:.2f} $")
        sys.exit(1)
    print(f"[OK] Python == JS == {total_py_cents / 100:.2f} $ (production_valeur=96)")
    sys.exit(0)
